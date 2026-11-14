"""support_kit.py — buyer support files inside every product pack.

``build(pack_dir)`` inspects the real files in a pack (or the members of its
``product.zip``) and writes a deterministic ``support/`` folder:

  HOW-TO-PRINT.md               A4 / Letter, scaling, duplex and paper tips
  HOW-TO-USE-<FORMAT>.md        one guide per format that is really present
  FAQ.md                        questions answered from the pack's own contents
  LICENSE-PERSONAL.txt          personal-use licence template
  LICENSE-COMMERCIAL-NOTE.txt   commercial-use note (the owner decides)
  SUPPORT-REPLIES.md            polite owner reply templates, [amount] only
  THANK-YOU-QUICK-START.pdf     one-page thank-you + quick start (pdf_design.Pdf)
  OWNER-NOTES.md                Tenglish notes for the owner (not for buyers)

Buyer files are English. Nothing is uploaded, sold or emailed; the owner reads
OWNER-NOTES.md and decides about licences and refunds. The build is idempotent:
unchanged files are not rewritten and no generated text contains a timestamp or
a money amount.
"""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import zipfile

import pdf_design

BASE_DIR = Path(__file__).resolve().parent
SUPPORT_DIRNAME = "support"
THANK_YOU_PDF = "THANK-YOU-QUICK-START.pdf"
OWNER_NOTES = "OWNER-NOTES.md"
REQUIRED_FILES = (
    "HOW-TO-PRINT.md",
    "FAQ.md",
    "LICENSE-PERSONAL.txt",
    "LICENSE-COMMERCIAL-NOTE.txt",
    "SUPPORT-REPLIES.md",
    THANK_YOU_PDF,
    OWNER_NOTES,
)

FORMAT_LABELS = {
    "sheets": "Spreadsheet (.xlsx / .csv)",
    "pdf": "Printable PDF",
    "pdf_fillable": "Fillable PDF",
    "notion": "Notion template",
    "resume": "Word resume / cover letter (.docx)",
    "docx": "Word document (.docx)",
    "calendar": "Calendar file (.ics)",
    "images": "Images (.png / .jpg)",
    "text": "Text / Markdown files",
}

HOW_TO_USE = {
    "sheets": (
        "HOW-TO-USE-SHEETS.md",
        "How to use the spreadsheet files",
        [
            "Open the .xlsx file in Excel or a spreadsheet app that reads .xlsx files.",
            "Allow editing if your app asks. The workbook is not protected.",
            "Look at the Instructions tab first; it lists every tab and what it does.",
            "Type only in the white input cells. Formula cells calculate by themselves.",
            "Save a copy before you change anything, so the original stays clean.",
            "CSV files open in any spreadsheet app; keep them in the same folder as the workbook.",
            "If a formula shows as text, set the cell format to General and re-enter the value.",
        ],
    ),
    "pdf": (
        "HOW-TO-USE-PDF.md",
        "How to use the printable PDF files",
        [
            "Open the PDF in any free PDF reader on computer, tablet or phone.",
            "Print at 100 percent (Actual size) so the layout keeps its real size.",
            "Both A4 and US Letter files are included when the pack says so; pick the one for your printer.",
            "Use plain paper for writing pages and thicker paper only for a cover if you like.",
            "The PDF is for personal printing; see LICENSE-PERSONAL.txt.",
        ],
    ),
    "pdf_fillable": (
        "HOW-TO-USE-PDF-FILLABLE.md",
        "How to use the fillable PDF files",
        [
            "Open the PDF in a reader that supports form fields (for example Adobe Acrobat Reader).",
            "Click a field and type. Press Tab to move to the next field.",
            "Save a copy with a new name after you fill it in.",
            "Print the filled copy, or keep it as a digital file.",
            "If a field does not accept text, your reader may be in view-only mode; switch to edit mode.",
        ],
    ),
    "notion": (
        "HOW-TO-USE-NOTION.md",
        "How to import the Notion template",
        [
            "Download and unzip the Notion zip file if your store gives you a .zip.",
            "In Notion, open Settings, then Import, and choose Markdown and CSV.",
            "Select the unzipped .md and .csv files and confirm the import.",
            "Open the imported page and move it into the workspace you want.",
            "CSV files become Notion databases; the .md pages become normal pages.",
        ],
    ),
    "resume": (
        "HOW-TO-USE-RESUME.md",
        "How to edit the resume and cover letter",
        [
            "Open the .docx files in Microsoft Word, Google Docs or LibreOffice Writer.",
            "Replace the placeholder name, contact line and summary with your own details.",
            "Keep one page for the resume unless the instructions say longer is fine.",
            "Check spelling and keep the file as .docx when you send it to an employer.",
            "Use the tracker spreadsheet to record where you sent each version.",
        ],
    ),
    "docx": (
        "HOW-TO-USE-WORD.md",
        "How to use the Word documents",
        [
            "Open the .docx files in Microsoft Word, Google Docs or LibreOffice Writer.",
            "Replace placeholder text with your own details.",
            "Use Save As to keep your edited copy separate from the original.",
            "Export to PDF only after you finish editing.",
        ],
    ),
    "calendar": (
        "HOW-TO-USE-CALENDAR.md",
        "How to import the calendar file",
        [
            "Open your calendar app and find its Import command.",
            "Choose the .ics file from this pack.",
            "Pick the calendar where the events should appear and confirm the import.",
            "Check one or two events after importing to confirm the dates.",
        ],
    ),
    "images": (
        "HOW-TO-USE-IMAGES.md",
        "How to use the image files",
        [
            "Open the .png or .jpg files in any photo viewer.",
            "Keep the original files unchanged; edit a copy if you need a different size.",
            "For printing, use the largest file in the pack for the best result.",
        ],
    ),
    "text": (
        "HOW-TO-USE-TEXT.md",
        "How to use the text and Markdown files",
        [
            "Open .md files in any text editor or Markdown viewer.",
            "Open .txt files in Notepad, TextEdit or any text editor.",
            "Keep the line breaks when you copy text into another app.",
        ],
    ),
}


def _display_name(pack_dir: Path) -> str:
    name = pack_dir.name.strip() or "Product pack"
    if "-" in name:
        head, tail = name.split("-", 1)
        if head.isdigit():
            name = tail
    name = name.replace("_", " ").replace("-", " ").strip()
    return name.title() if name else "Product pack"


def _iter_pack_files(pack_dir: Path, exclude=None):
    support_dir = (Path(exclude) if exclude else (pack_dir / SUPPORT_DIRNAME)).resolve()
    for path in sorted(pack_dir.rglob("*")):
        if not path.is_file():
            continue
        try:
            path.resolve().relative_to(support_dir)
            continue
        except ValueError:
            pass
        if path.name.startswith("."):
            continue
        yield path.name, path.suffix.lower(), path.stat().st_size


def _zip_pack_files(pack_dir: Path):
    archive = pack_dir / "product.zip"
    if not archive.is_file():
        return []
    out = []
    try:
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                if info.is_dir() or info.filename.startswith("."):
                    continue
                name = Path(info.filename).name
                if not name or name.startswith("."):
                    continue
                out.append((name, Path(name).suffix.lower(), info.file_size))
    except (OSError, zipfile.BadZipFile):
        return []
    return out


def pack_files(pack_dir, exclude=None) -> list[dict]:
    """Real buyer files in the pack, sorted by name (zip members included)."""
    root = Path(pack_dir)
    support_dir = Path(exclude) if exclude else root / SUPPORT_DIRNAME
    seen = {}
    for name, ext, size in list(_iter_pack_files(root, support_dir)) + _zip_pack_files(root):
        key = name.casefold()
        if key not in seen or size > seen[key]["size"]:
            seen[key] = {"name": name, "ext": ext, "size": int(size)}
    return [seen[key] for key in sorted(seen)]


def _pdf_is_fillable(path: Path) -> bool:
    try:
        data = path.read_bytes()[:512 * 1024]
    except OSError:
        return False
    return b"/AcroForm" in data or b"/Widget" in data


def _zip_has_fillable_pdf(pack_dir: Path) -> bool:
    archive = pack_dir / "product.zip"
    if not archive.is_file():
        return False
    try:
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                if info.is_dir() or Path(info.filename).suffix.lower() != ".pdf":
                    continue
                with zf.open(info) as handle:
                    head = handle.read(512 * 1024)
                if b"/AcroForm" in head or b"/Widget" in head:
                    return True
    except (OSError, zipfile.BadZipFile):
        return False
    return False


def detect_formats(pack_dir, exclude=None) -> list[str]:
    """Format ids really present in the pack, sorted, never guessed from the folder name."""
    root = Path(pack_dir)
    files = pack_files(root, exclude)
    exts = {item["ext"] for item in files}
    names = [item["name"].casefold() for item in files]
    formats = set()
    notionish = any("notion" in n for n in names) or "how_to_import.md" in names
    notion = notionish or (".md" in exts and (".csv" in exts) and not (exts & {".xlsx", ".xls"}))
    has_workbook = bool(exts & {".xlsx", ".xls", ".tsv"})
    if has_workbook or (".csv" in exts and not (notion and not has_workbook)):
        formats.add("sheets")
    if ".pdf" in exts:
        fillable = _zip_has_fillable_pdf(root)
        for item in files:
            if item["ext"] == ".pdf":
                candidate = root / item["name"]
                if candidate.is_file() and _pdf_is_fillable(candidate):
                    fillable = True
                    break
        formats.add("pdf_fillable" if fillable else "pdf")
    if exts & {".docx", ".doc"}:
        if any("resume" in n or "cover" in n for n in names) or "job" in " ".join(names):
            formats.add("resume")
        else:
            formats.add("docx")
    if ".ics" in exts:
        formats.add("calendar")
    if exts & {".png", ".jpg", ".jpeg", ".webp"}:
        formats.add("images")
    if notion:
        formats.add("notion")
    if not formats and exts & {".md", ".txt"}:
        formats.add("text")
    return sorted(formats)


def _format_list_text(formats):
    if not formats:
        return "- Text and support files only"
    return "\n".join(f"- {FORMAT_LABELS.get(fmt, fmt)}" for fmt in formats)


def _how_to_print(pack_name: str) -> str:
    return f"""# How to print — {pack_name}

These steps work with any normal home or office printer.

## Paper size
1. Open the PDF in your PDF reader.
2. In the print dialog choose **A4** or **US Letter**. Use the size written on the file name when both are included.
3. If the page looks cropped, choose **Fit to printable area** once and compare; if it looks small, switch back to **Actual size**.

## Scaling
- Start with **100% / Actual size**. The design is made for that setting.
- Use **Fit** only when your printer cannot print close to the paper edge.
- Never use a scale above 100% for page layouts; text can be cut at the edge.

## Duplex (both sides)
- Choose **Flip on long edge** for normal portrait pages.
- If your printer feeds paper face up, choose the other duplex option and print one test page first.
- Thin paper may show ink from the other side; use 80 gsm or thicker if that bothers you.

## Paper and ink
- Plain 80–100 gsm paper is enough for writing pages.
- Use thicker paper only for a cover or a page you want to keep for a long time.
- Draft or eco mode saves ink; normal mode gives darker lines.
- Let pages dry for a minute before stacking them if your printer uses a lot of ink.

## Before you print many copies
1. Print page 1 only.
2. Check the size, margins and readability.
3. Then print the rest.
"""


def _how_to_use(fmt: str, pack_name: str) -> tuple[str, str]:
    filename, title, steps = HOW_TO_USE[fmt]
    body = [f"# {title} — {pack_name}", ""]
    for index, step in enumerate(steps, 1):
        body.append(f"{index}. {step}")
    body.extend([
        "",
        "## If something looks wrong",
        "- Re-download the file from the store before you edit it.",
        "- Make a copy before changing anything.",
        "- See FAQ.md and LICENSE-PERSONAL.txt in this support folder.",
        "",
    ])
    return filename, "\n".join(body)


def _faq(pack_name: str, formats, files) -> str:
    names = [item["name"] for item in files]
    shown = names[:12]
    file_lines = "\n".join(f"- {name}" for name in shown)
    if len(names) > len(shown):
        file_lines += f"\n- and {len(names) - len(shown)} more file(s) in the pack"
    if not file_lines:
        file_lines = "- The pack folder was empty when this guide was made."

    answers = [
        ("What is in this pack?", f"This pack contains {len(names)} file(s):\n{file_lines}"),
        ("Which formats are included?", _format_list_text(formats)),
        ("Which file should I open first?",
         "Open README.md when it is present; it lists the files and the suggested order. "
         "Otherwise open the first file in the list above that matches what you bought."),
        ("What software do I need?",
         _software_answer(formats)),
        ("Can I print the files?",
         "Yes, when the pack includes a PDF. Read HOW-TO-PRINT.md for paper size, scaling and duplex tips."
         if any(f in formats for f in ("pdf", "pdf_fillable"))
         else "This pack is digital only; print the files only when your app offers a print option."),
        ("Can I use the files on a phone or tablet?",
         "You can open most files on a phone or tablet. Editing spreadsheets or Word files is easier on a computer."),
        ("Is this for personal use?",
         "Yes. LICENSE-PERSONAL.txt explains personal use. Read it before you share anything."),
        ("Can I resell or share the files?",
         "No, not under the personal licence. See LICENSE-COMMERCIAL-NOTE.txt if you need commercial use; the seller decides that separately."),
        ("The download stopped or the file will not open. What now?",
         "Download it again from the store. If it still fails, contact the seller through the store and mention the file name."),
        ("Can I get a refund?",
         "Refunds follow the store's policy. Contact the seller through the store and explain what went wrong; the seller will review it."),
        ("Will I get future updates?",
         "That depends on the seller and the store listing. Check the listing page for the current promise."),
    ]
    lines = [f"# Buyer FAQ — {pack_name}", ""]
    for question, answer in answers:
        lines.extend([f"## {question}", answer, ""])
    return "\n".join(lines)


def _software_answer(formats):
    needs = []
    if "sheets" in formats:
        needs.append("Excel or another app that opens .xlsx files")
    if any(f in formats for f in ("pdf", "pdf_fillable")):
        needs.append("a free PDF reader")
    if "notion" in formats:
        needs.append("a free Notion account")
    if "resume" in formats or "docx" in formats:
        needs.append("Microsoft Word, Google Docs or LibreOffice Writer")
    if "calendar" in formats:
        needs.append("a calendar app that imports .ics files")
    if not needs:
        needs.append("a text editor or the app that matches your files")
    return "You need " + ", ".join(needs) + ". No paid software is required for the files themselves."


def _license_personal(pack_name: str) -> str:
    return f"""PERSONAL USE LICENCE — {pack_name}

This template is written for the buyer of this pack. The seller may edit it
before publishing, but must not make promises that are not true.

1. You may use these files for your own personal use.
2. You may print the files for yourself and for people in your own household.
3. You may not sell, resell, sublicense, publish or give away the files.
4. You may not upload the files to a public file-sharing site or a template marketplace.
5. You may not claim that you created the original template.
6. Keep this licence file with the pack.

The files are provided as they are. The seller does not promise a particular
result, income, job, health outcome or exam score.

Questions about this licence go to the seller through the store where you bought it.
"""


def _license_commercial(pack_name: str) -> str:
    return f"""COMMERCIAL USE NOTE — {pack_name}

This is a note for the seller and the buyer. It is not an automatic commercial
licence. The seller decides whether commercial use is allowed and on what terms.

FOR THE SELLER
- Decide: is commercial use allowed, not allowed, or allowed for [amount]?
- Write the final answer in your own words before publishing the pack.
- Do not promise income, sales, clients or any business result.
- Keep a copy of the final terms with the pack.

FOR THE BUYER
- Personal use is covered by LICENSE-PERSONAL.txt.
- Commercial use is not included unless the seller says so in writing.
- If you need commercial use, contact the seller through the store.
- Do not resell, share or republish the files until the seller confirms the terms.
- Any agreed commercial fee is a separate decision between buyer and seller; see [amount] in the seller's terms.

No promise of income, sales, clients or any other result is made here.
"""


def _support_replies(pack_name: str) -> str:
    return f"""# Support reply templates — {pack_name}

These are templates for the seller. Keep replies short and polite. Never promise
a refund, a result or a delivery time that you cannot keep. The seller sends the
reply by hand after reading the buyer's message.

## Template 1 — download problem
Subject: Your download for {pack_name}

Hello,

Thank you for buying {pack_name}. I am sorry the download is not working.

Please try these steps:
1. Open the store receipt and download the pack again.
2. Use a normal web browser on a computer if the phone download stops.
3. Check that the file finished downloading before opening it.

If it still does not open, reply with the file name and a short description of
what you see. I will look into it and reply as soon as I can.

Thank you for your patience.

## Template 2 — wrong size
Subject: Page size for {pack_name}

Hello,

Thank you for telling me about the page size. The pack is made for A4 and US
Letter where the listing says so.

Please check the paper size in your print dialog and choose the matching file.
If the wrong page size is still a problem, send me a photo of the print settings
and I will review it.

I cannot promise a different size until I check the files and the store listing.

## Template 3 — refund request
Subject: Your refund request for {pack_name}

Hello,

Thank you for your message and for explaining the problem. I am sorry the pack
did not work for you.

I will review the request against the store's refund policy and the details you
sent. If a refund is approved, the amount will be handled by the store where you
bought the pack, using the original payment method. The listing shows [amount]
as the price; any refund amount follows the store's policy.

I cannot promise a refund before this review. I will reply after I have checked
the details.

Thank you for your understanding.
"""


def _owner_notes(pack_name: str, formats, files) -> str:
    format_text = ", ".join(FORMAT_LABELS.get(f, f) for f in formats) or "text only"
    kinds = "\n".join(f"- {item['name']}" for item in files[:15]) or "- (files dorakaledu)"
    return f"""# Owner notes — {pack_name}

Idi buyer ki kaadu; owner ki matrame. Support folder lo emi unnayo ikkada summary.

## Pack lo nijamga unna formats
{format_text}

## Nijamga unna files (modati 15)
{kinds}

## Buyer files
- `HOW-TO-PRINT.md` — printing steps (A4 / Letter, scaling, duplex, paper tips).
- `HOW-TO-USE-*.md` — okko format ki separate guide.
- `FAQ.md` — pack contents nunchi answers; reviews ledu, numbers ledu.
- `LICENSE-PERSONAL.txt` — personal use template. Owner final wording check cheyali.
- `LICENSE-COMMERCIAL-NOTE.txt` — commercial use owner decide cheyali; automatic ga ivvakoodadu.
- `SUPPORT-REPLIES.md` — download problem, wrong size, refund request templates.
- `{THANK_YOU_PDF}` — one-page thank you + quick start.

## Owner decide cheyalsinavi
1. Commercial use allowed aa, not allowed aa, leda [amount] ki allowed aa — final wording rasi pack lo pettali.
2. Refund policy store listing lo clear ga undali; templates lo promise emi ledu.
3. Buyer support email / store contact place pettali (ee templates lo ledu).
4. Price and store text owner set chestadu; ee folder lo money amounts levu.

## Safety
Nothing auto-publish avvadu, auto-sell avvadu, auto-email avvadu. Buyer ki pampadaniki mundu owner okkasaari chadavalii.
"""


def _write_if_changed(path: Path, data: bytes) -> bool:
    if path.is_file():
        try:
            if path.read_bytes() == data:
                return False
        except OSError:
            pass
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix=".support-", dir=str(path.parent))
    try:
        with os.fdopen(handle, "wb") as handle_file:
            handle_file.write(data)
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    return True


def _thank_you_pdf(path: Path, pack_name: str, formats, files) -> None:
    pdf = pdf_design.Pdf("A4", footer=f"{pack_name} · buyer support", style="A")
    pdf.new_page()
    width, height = pdf.w, pdf.h
    left, right = pdf_design.MARGIN, width - pdf_design.MARGIN
    ink = pdf_design.STYLES["A"]["ink"]
    muted = pdf_design.STYLES["A"]["muted"]
    accent = pdf_design.STYLES["A"]["accent"]

    pdf.fill(left, height - 150, right - left, 92, pdf_design.STYLES["A"]["bar"])
    pdf.text(left + 22, height - 92, "Thank you!", 30, True, accent)
    lines = pdf_design.wrap_text(f"Quick start for {pack_name}", 13, right - left - 44)
    y = height - 122
    for line in lines[:2]:
        pdf.text(left + 22, y, line, 13, False, ink)
        y -= 17

    pdf.text(left, height - 190, "Your quick start", 17, True, ink)
    steps = [
        "Open README.md or the first file listed on the store page.",
        "Read the HOW-TO-USE guide for your file type.",
        "Make a copy before you edit or print anything.",
        "Print the PDF at 100 percent / Actual size.",
        "Keep LICENSE-PERSONAL.txt with your files.",
    ]
    y = height - 222
    for number, step in enumerate(steps, 1):
        pdf.circle(left + 8, y + 3, 8, fill=accent)
        pdf.text_center(left + 8, y, str(number), 10, True, "#ffffff")
        wrapped = pdf_design.wrap_text(step, 12, right - left - 30)
        for line in wrapped[:2]:
            pdf.text(left + 26, y, line, 12, False, ink)
            y -= 15
        y -= 8
        if y < pdf_design.BOTTOM + 150:
            break

    y -= 6
    pdf.line(left, y + 12, right, y + 12, color=pdf_design.STYLES["A"]["line"])
    y -= 8
    pdf.text(left, y, "What is inside", 15, True, ink)
    y -= 20
    shown = [item["name"] for item in files[:8]]
    if shown:
        for name in shown:
            for line in pdf_design.wrap_text(f"- {name}", 10, right - left)[:1]:
                pdf.text(left, y, line, 10, False, muted)
                y -= 13
        if len(files) > len(shown):
            pdf.text(left, y, f"- and {len(files) - len(shown)} more file(s)", 10, False, muted)
            y -= 13
    else:
        pdf.text(left, y, "- The support files in this folder", 10, False, muted)
        y -= 13

    y -= 12
    pdf.text(left, y, "Need help?", 15, True, ink)
    y -= 20
    help_lines = pdf_design.wrap_text(
        "Contact the seller through the store where you bought this pack. "
        "Include the file name and what you see. See FAQ.md and SUPPORT-REPLIES.md in this folder.",
        11, right - left)
    for line in help_lines[:4]:
        pdf.text(left, y, line, 11, False, muted)
        y -= 15

    y -= 6
    pdf.text(left, y, "For personal use. See LICENSE-PERSONAL.txt.", 10, False, accent)
    pdf.save(path, title=f"Thank you — {pack_name}", meta={"Subject": "Buyer quick start"})
    if len(pdf.pages) != 1:
        raise RuntimeError("thank-you guide must be exactly one page")


def build(pack_dir, support_dir=None) -> dict:
    """Write (or refresh) the support folder for one pack. Returns a manifest dict."""
    root = Path(pack_dir)
    if not root.is_dir():
        raise NotADirectoryError(str(root))
    target = Path(support_dir) if support_dir else root / SUPPORT_DIRNAME
    pack_name = _display_name(root)
    files = pack_files(root, target)
    formats = detect_formats(root, target)

    documents = [("HOW-TO-PRINT.md", _how_to_print(pack_name))]
    for fmt in formats:
        if fmt in HOW_TO_USE:
            filename, text = _how_to_use(fmt, pack_name)
            documents.append((filename, text))
    documents.extend([
        ("FAQ.md", _faq(pack_name, formats, files)),
        ("LICENSE-PERSONAL.txt", _license_personal(pack_name)),
        ("LICENSE-COMMERCIAL-NOTE.txt", _license_commercial(pack_name)),
        ("SUPPORT-REPLIES.md", _support_replies(pack_name)),
        (OWNER_NOTES, _owner_notes(pack_name, formats, files)),
    ])

    written, unchanged = [], []
    for filename, text in documents:
        if _write_if_changed(target / filename, text.encode("utf-8")):
            written.append(filename)
        else:
            unchanged.append(filename)

    pdf_path = target / THANK_YOU_PDF
    handle, temp_name = tempfile.mkstemp(prefix=".support-", suffix=".pdf", dir=str(target))
    os.close(handle)
    try:
        _thank_you_pdf(Path(temp_name), pack_name, formats, files)
        new_bytes = Path(temp_name).read_bytes()
        if _write_if_changed(pdf_path, new_bytes):
            written.append(THANK_YOU_PDF)
        else:
            unchanged.append(THANK_YOU_PDF)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)

    expected = sorted([name for name, _ in documents] + [THANK_YOU_PDF])
    for path in sorted(target.iterdir()):
        if path.is_file() and path.name.startswith(".support-"):
            path.unlink()
    return {
        "pack": pack_name,
        "folder": str(root),
        "support_dir": str(target),
        "formats": formats,
        "buyer_files": [item["name"] for item in files],
        "written": sorted(written),
        "unchanged": sorted(unchanged),
        "files": expected,
    }


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description="Write buyer support files inside a pack folder.")
    parser.add_argument("pack_dir", help="product or upload-pack folder")
    parser.add_argument("--out", default=None, help="support folder (default <pack>/support)")
    args = parser.parse_args(argv)
    result = build(args.pack_dir, args.out)
    print(f"Support files for {result['pack']}")
    print(f"Formats: {', '.join(result['formats']) or 'none detected'}")
    print(f"Written: {len(result['written'])}, unchanged: {len(result['unchanged'])}")
    for name in result["files"]:
        print(f"  {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
