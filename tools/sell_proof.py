"""tools/sell_proof.py — proof that every product type can be sold (CCC-10).

Builds ONE pack of each registered product type in a temporary folder through the robot's real path:
  product_builder.build_product -> product_builder.truth_check_folder (Truth Checker + upload pack +
  the listed buyer files in product.zip) -> store_kits (Gumroad kit) -> gumroad_images -> marketing_auto
  (private marketing files) -> sell_ready.evaluate_pack (the /sell evaluation) -> tg_sell (/sell message).
It also checks that product.zip holds every file the listing promises under "What you get".

Prints one line per type: type | status (SELLABLE / ALMOST / NOT YET) | truth check | zip | top fixes.
Nothing is uploaded, posted, sold or emailed. No network. Nothing is written into the repo folder.

Usage:
  python3 tools/sell_proof.py              # table
  python3 tools/sell_proof.py --json       # machine-readable
  python3 tools/sell_proof.py --keep DIR   # keep the built packs in DIR (must be outside the repo)
  python3 tools/sell_proof.py --only flashcards,wall_art
"""
from __future__ import annotations

import argparse
import contextlib
import copy
import io
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import format_registry

FORMAT_ORDER = format_registry.list_registered_formats()

TAGS = ["printable", "organizer", "planner", "checklist", "worksheet", "digital download", "personal use",
        "simple layout", "productivity", "routine", "goal setting", "home office", "template"]
BASE_DESC = ("A carefully designed digital product that helps you stay organised at home, at work and on the go. "
             "The files are ready to print or use right away, with simple instructions and a calm, clean "
             "layout. Every page and template is made for personal use, so you can copy it as often as you "
             "need for your own routine.")


def _listing(title: str, description: str = BASE_DESC) -> Dict[str, Any]:
    return {"title": title, "description": description, "tags": list(TAGS), "buyer": "adults",
            "platform": "Gumroad"}


def _custom(kind: str, title: str, short: str, **extra: Any) -> Dict[str, Any]:
    spec = {"kind": kind, "name": title, "short_name": short, "title": title,
            "cover_image_idea": f"{title} on a clean desk", "instructions": ["Open the files and print at 100%."],
            "listing": _listing(title)}
    spec.update(extra)
    return spec


def builtin_specs() -> Dict[str, tuple]:
    """(raw spec, niche) for the five built-in kinds; parsed with the real product_builder.parse_spec."""
    notion_md = ("This client workspace keeps every lead, conversation, follow-up and project note in one calm "
                 "place. Use the Clients database for contact details and the Projects database for active "
                 "work. Add a row for each person, choose a status and update the next follow-up date after "
                 "every conversation. Keep the page open during calls so every promise, question and next step "
                 "is written down before the call ends. At the end of the day, clear the inbox and add fresh "
                 "notes to the right client row. When a project finishes, move it to the archive and leave a "
                 "short summary for the next review. Once a week, sort the Clients database by follow-up "
                 "date and work from the oldest open lead down, so nobody waits too long for an answer. ")
    return {
        "sheets": ({
            "kind": "sheets", "name": "Budget Sheet", "short_name": "budget-sheet",
            "cover_image_idea": "Clean spreadsheet", "instructions": ["Open the workbook."],
            "listing": _listing("Freelancer Budget Spreadsheet",
                                "Includes one Excel workbook with an Income Log tab and automatic balance formulas. "
                                "It also has a status options menu and sample rows you can replace. The workbook "
                                "helps you track payments and see your balance update as you type. Personal use only."),
            "sheets": [{"name": "Income Log",
                        "columns": [{"name": "Date", "type": "date"},
                                    {"name": "Client", "type": "text", "options": ["Client A", "Client B"]},
                                    {"name": "Amount", "type": "money"},
                                    {"name": "Status", "type": "text", "options": ["Paid", "Unpaid"]}],
                        "rows": [["2026-01-05", "Client A", 450, "Paid"], ["2026-01-09", "Client B", 1200, "Unpaid"]],
                        "overdue": {"due": "Date", "status": "Status", "done": "Paid"},
                        "balance": {"in": "Amount", "out": ""}}],
        }, "Budget Sheets Agent"),
        "planner": ({
            "kind": "planner", "name": "Focus Planner", "short_name": "focus-planner",
            "cover_image_idea": "Calm desk with planner", "instructions": ["Print at 100%."],
            "listing": _listing("Focus Planner Printable"),
            "pages": [{"title": "Daily Plan", "subtitle": "Plan your day", "sections": [
                {"heading": "Top 3", "type": "lines", "count": 3}, {"heading": "Notes", "type": "notes", "count": 6}]}],
        }, "ADHD Planners Agent"),
        "notion": ({
            "kind": "notion", "name": "Client CRM", "short_name": "client-crm",
            "cover_image_idea": "Laptop dashboard", "instructions": ["Import the files."],
            "listing": _listing("Client CRM Template for Freelancers",
                                "Includes two import pages and two databases for clients and projects. The Clients "
                                "database holds contact details and the Projects database keeps active work in one "
                                "view. Every row is an example you can replace. Follow the import guide to load the "
                                "files into your workspace. Personal use only."),
            "pages": [{"title": "Start Here", "markdown": "# Start Here\n\n" + notion_md},
                      {"title": "Weekly Review", "markdown": "# Weekly Review\n\n" + notion_md}],
            "databases": [
                {"name": "Clients", "columns": ["Name", "Status", "Follow up", "Notes"],
                 "rows": [[f"Client {c}", s, f"2026-01-1{n}", "Next step noted"]
                          for n, (c, s) in enumerate(zip("ABCDE", ["Lead", "Active", "Past", "Lead", "Active"]))]},
                {"name": "Projects", "columns": ["Project", "Status", "Due", "Owner"],
                 "rows": [["Website refresh", "Active", "2026-02-01", "Client B"],
                          ["Brand guide", "Draft", "2026-01-30", "Client D"],
                          ["Newsletter", "Done", "2025-12-15", "Client A"],
                          ["Launch plan", "Active", "2026-02-10", "Client E"]]}],
        }, "Notion Templates Agent"),
        "resume": ({
            "kind": "resume", "name": "ATS Resume Bundle", "short_name": "ats-resume",
            "cover_image_idea": "Resume on desk", "instructions": ["Open the files."],
            "listing": _listing("ATS Resume and Cover Letter Bundle",
                                "Includes a Word resume, a Word cover letter and an Excel job tracker with a Job "
                                "Applications tab and automatic overdue formulas. Replace the placeholders with your "
                                "own details. The single-column layout stays easy for applicant tracking systems to "
                                "read. Personal use only."),
            "resume": {"sections": [{"heading": "Professional Summary", "bullets": ["[Two lines about you]"]},
                                    {"heading": "Experience", "bullets": ["[Job Title], [Company]"]}]},
            "cover_letter": {"paragraphs": ["Dear [Hiring Manager Name],", "[Why this role]", "[Your Name]"]},
            "tracker": {"name": "Job Applications",
                        "columns": [{"name": "Company", "type": "text", "options": ["Acme"]},
                                    {"name": "Role", "type": "text", "options": ["Designer"]},
                                    {"name": "Applied", "type": "date"}, {"name": "Follow-up", "type": "date"},
                                    {"name": "Status", "type": "text", "options": ["Open", "Closed"]}],
                        "rows": [["[Company A]", "[Role]", "2026-01-05", "2026-01-12", "Open"]],
                        "overdue": {"due": "Follow-up", "status": "Status", "done": "Closed"}},
        }, "Resume Bundle Agent"),
        "dated_planner": ({
            "kind": "dated_planner", "name": "2027 Dated Planner", "short_name": "planner-2027",
            "cover_image_idea": "Calendar preview", "span": "2027", "start_day": "Monday",
            "instructions": ["Print at 100%."], "listing": _listing("2027 Dated Planner Calendar"),
        }, "ADHD Planners Agent"),
    }


CUSTOM_SPECS = {
    "event_planner": _custom("event_planner", "Indian Wedding Planner Printable", "wedding-planner",
                             event_type="wedding", edition="indian"),
    "flashcards": _custom("flashcards", "Alphabet Flashcards for Kids", "alphabet-flashcards", topic="alphabet"),
    "habit_tracker": _custom("habit_tracker", "Monthly Habit Tracker Printable", "habit-tracker"),
    "recipe_book": _custom("recipe_book", "Everyday Recipe Book and Meal Planner", "recipe-book"),
    "email_signature": _custom("email_signature", "Email Signature Templates Pack", "email-signature"),
    "study_planner": _custom("study_planner", "Exam and Study Planner Printable", "study-planner"),
    "wall_art": _custom("wall_art", "Mindful Wall Art Print Set", "wall-art"),
    'teacher_planner': _custom('teacher_planner', 'Teacher Planner Printable', 'teacher-planner'),
    'fitness_planner': _custom('fitness_planner', 'Fitness Planner Printable', 'fitness-planner'),
    'baby_tracker': _custom('baby_tracker', 'Baby Tracker Printable', 'baby-tracker'),
    'small_business_log': _custom('small_business_log', 'Small Business Log Printable', 'small-business-log'),
    'kids_chore_reward': _custom('kids_chore_reward', 'Kids Chore and Reward Pack Printable', 'kids-chore-reward'),
}


def spec_for(kind: str) -> Dict[str, Any]:
    import product_builder as pb
    builtin = builtin_specs()
    if kind in builtin:
        raw, niche = builtin[kind]
        parsed, _reason = pb.parse_spec(json.dumps(copy.deepcopy(raw)), niche)
        return parsed or copy.deepcopy(raw)
    return copy.deepcopy(CUSTOM_SPECS[kind])


def zip_gaps(product_folder: Path, pack: Path) -> List[str]:
    """Files promised under 'What you get' that are neither in product.zip nor a listing file."""
    import product_builder as pb
    listing = (product_folder / "listing.md").read_text(encoding="utf-8")
    with zipfile.ZipFile(pack / "product.zip") as archive:
        present = set(archive.namelist())
    return [name for name in pb.listed_files(listing) if name not in present]


def _sell_message(approval_id: int, products: Path, marketing: Path) -> Dict[str, Any]:
    """Run the real /sell plugin with recording callbacks (nothing is sent anywhere)."""
    import tg_sell
    texts: List[str] = []
    documents: List[str] = []
    photos: List[str] = []
    ctx = {"db_path": None, "chat_id": "0", "products_dir": str(products), "marketing_dir": str(marketing),
           "send_text": lambda text, keyboard=None: texts.append(text) or True,
           "send_document": lambda path, caption="": documents.append(str(path)) or True,
           "send_photo": lambda path, caption="": photos.append(str(path)) or True,
           "send_media_group": lambda paths, caption="": True,
           "state_get": lambda key, default=None: default, "state_set": lambda key, value: None}
    reply = tg_sell.handle(f"/sell {approval_id}", ctx) or {}
    if reply.get("text"):
        texts.append(reply["text"])
    joined = "\n".join(texts)
    return {"ok": "GUMROAD FIELDS" in joined and bool(documents) and bool(photos),
            "documents": len(documents), "photos": len(photos)}


def prove(kind: str, root: Path) -> Dict[str, Any]:
    """Build one pack of `kind` under root and evaluate it like /sell. Never raises."""
    import product_builder as pb
    row: Dict[str, Any] = {"type": kind, "status": "NOT YET", "truth": "-", "zip": "-", "sell_message": False,
                           "fixes": [], "pack": ""}
    approval_id = FORMAT_ORDER.index(kind) + 1
    products = root / kind / "products"
    products.mkdir(parents=True, exist_ok=True)
    db_path = str(root / kind / "proof.db")
    try:
        folder_name = pb.build_product(spec_for(kind), approval_id, out_dir=str(products))
        product_folder = products / folder_name
        refused = io.StringIO()
        with contextlib.redirect_stderr(refused):
            truth = pb.truth_check_folder(approval_id, folder_name, db_path=db_path, out_dir=str(products),
                                          notify_fn=lambda text: True)
        row["truth"] = f"{truth['verdict']} {truth['score']}"
        pack = products / "upload" / folder_name
        if truth["verdict"] != "ready":
            row["fixes"] = [f"Truth check: {p}" for p in truth["problems"][:3]]
            return row
        if not (pack / "product.zip").is_file():
            row["fixes"] = [refused.getvalue().strip() or "upload pack was not made"]
            return row
        row["pack"] = str(pack)
        import gumroad_images
        import marketing_auto
        import sell_ready
        import store_kits
        store_kits.build(pack)
        if gumroad_images.available():
            gumroad_images.for_pack(pack)
        marketing_auto.build_for_pack(pack, db_path, products_dir=products)
        marketing = marketing_auto.marketing_root(products)
        evaluation = sell_ready.evaluate_pack(pack, marketing)
        row["status"] = evaluation["status"]
        row["fixes"] = list(evaluation.get("top_fixes") or [])[:3]
        gaps = zip_gaps(product_folder, pack)
        row["zip"] = "ok" if not gaps else f"missing {len(gaps)}"
        if gaps:
            row["status"] = sell_ready.STATUS_NOT_YET
            row["fixes"].insert(0, "product.zip lacks listed files: " + ", ".join(gaps[:4]))
        message = _sell_message(approval_id, products, marketing)
        row["sell_message"] = message["ok"]
        if not message["ok"]:
            row["fixes"].append("/sell message incomplete (fields, files or pictures missing)")
    except Exception as error:   # one broken type must not hide the others
        row["fixes"] = [f"{type(error).__name__}: {error}"]
    return row


def run(kinds: Optional[List[str]] = None, keep: Optional[str] = None) -> List[Dict[str, Any]]:
    kinds = kinds or list(FORMAT_ORDER)
    if keep:
        root = Path(keep).expanduser().resolve()
        if root == ROOT or ROOT in root.parents:
            raise SystemExit("--keep must be outside the repo folder")
        root.mkdir(parents=True, exist_ok=True)
        return [prove(kind, root) for kind in kinds]
    with tempfile.TemporaryDirectory(prefix="sell_proof_") as temp:
        return [prove(kind, Path(temp)) for kind in kinds]


def table(rows: List[Dict[str, Any]]) -> str:
    lines = [f"{'Type':16} {'Status':9} {'Truth':10} {'Zip':10} {'/sell':5}  Top fixes",
             "-" * 100]
    for row in rows:
        fixes = "; ".join(row["fixes"]) if row["fixes"] else "-"
        lines.append(f"{row['type']:16} {row['status']:9} {row['truth']:10} {row['zip']:10} "
                     f"{'ok' if row['sell_message'] else 'no':5}  {fixes[:160]}")
    good = sum(1 for row in rows if row["status"] in ("SELLABLE", "ALMOST"))
    lines.append("-" * 100)
    lines.append(f"{good}/{len(rows)} SELLABLE or ALMOST")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Build one pack of every type and run the /sell check.")
    parser.add_argument("--json", action="store_true", help="print JSON instead of the table")
    parser.add_argument("--keep", help="keep the built packs in this folder (outside the repo)")
    parser.add_argument("--only", help="comma-separated types, e.g. flashcards,wall_art")
    args = parser.parse_args(argv)
    os.environ.setdefault("CHILLI_TEST", "1")    # offline: no AI calls from any step
    kinds = [k.strip() for k in args.only.split(",")] if args.only else None
    unknown = [k for k in kinds or [] if k not in FORMAT_ORDER]
    if unknown:
        parser.error(f"unknown type(s): {', '.join(unknown)}")
    rows = run(kinds, args.keep)
    print(json.dumps(rows, indent=2) if args.json else table(rows))
    return 0 if all(row["status"] in ("SELLABLE", "ALMOST") for row in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
