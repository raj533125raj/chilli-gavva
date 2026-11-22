"""OCC-1: end-to-end sell flow for every product format.

Each format is built with product_builder.build_product, staged as an upload pack
with product.zip + listing.txt + checklist.txt, checked by truth_check and
sell_ready, given real Gumroad store kits and images, and finally sent through
the /sell plugin via telegram_inbox.call_plugins with recorder callbacks.
"""
from __future__ import annotations

import copy
import json
import re
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import gumroad_images
import product_builder as pb
import sell_ready
import store_kits
import telegram_inbox
import tg_sell
import truth_check
import upload_pack

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    Image = None
    HAS_PIL = False

FORMAT_ORDER = [
    "sheets", "planner", "notion", "resume", "dated_planner",
    "event_planner", "flashcards", "habit_tracker", "recipe_book",
    "email_signature", "study_planner", "wall_art",
]
TRUTH_READY = {"sheets", "planner", "notion", "resume", "dated_planner"}
TAGS = [
    "printable", "organizer", "daily planner", "habit tracker", "goal setting",
    "digital download", "personal use", "simple layout", "home organization",
    "productivity", "routine", "checklist", "worksheet",
]
DESC = (
    "A complete, carefully designed digital product that helps you stay organized at home, "
    "at work, and on the go. The files are ready to print or use right away, with simple "
    "instructions and a calm, clean layout. Every page and template is made for personal use, "
    "so you can copy it as often as you need for your own routine."
)


def _listing(title, description=DESC):
    return {"title": title, "description": description, "tags": list(TAGS),
            "buyer": "adults", "platform": "Gumroad"}


BUILTIN_SPECS = {
    "sheets": ({
        "kind": "sheets", "name": "Budget Sheet", "short_name": "budget-sheet",
        "cover_image_idea": "Clean spreadsheet", "instructions": ["Open the workbook."],
        "listing": _listing(
            "Freelancer Budget Spreadsheet",
            "Includes one Excel workbook with an Income Log tab and automatic balance formulas. "
            "It also has a status options menu and sample rows you can replace. The workbook helps "
            "you track payments and see your balance update as you type. Personal use only."),
        "sheets": [{
            "name": "Income Log",
            "columns": [
                {"name": "Date", "type": "date"},
                {"name": "Client", "type": "text", "options": ["Client A", "Client B"]},
                {"name": "Amount", "type": "money"},
                {"name": "Status", "type": "text", "options": ["Paid", "Unpaid"]},
            ],
            "rows": [["2026-01-05", "Client A", 450, "Paid"],
                     ["2026-01-09", "Client B", 1200, "Unpaid"]],
            "overdue": {"due": "Date", "status": "Status", "done": "Paid"},
            "balance": {"in": "Amount", "out": ""},
        }],
    }, "Budget Sheets Agent"),
    "planner": ({
        "kind": "planner", "name": "Focus Planner", "short_name": "focus-planner",
        "cover_image_idea": "Calm desk with planner", "instructions": ["Print at 100%."],
        "listing": _listing("Focus Planner Printable"),
        "pages": [{"title": "Daily Plan", "subtitle": "Plan your day", "sections": [
            {"heading": "Top 3", "type": "lines", "count": 3},
            {"heading": "Notes", "type": "notes", "count": 6},
        ]}],
    }, "ADHD Planners Agent"),
    "notion": ({
        "kind": "notion", "name": "Client CRM", "short_name": "client-crm",
        "cover_image_idea": "Laptop dashboard", "instructions": ["Import the files."],
        "listing": _listing(
            "Client CRM Template for Freelancers",
            "Includes two import pages and two databases for clients and projects. The Clients "
            "database holds contact details and the Projects database keeps active work in one view. "
            "Every row is an example you can replace. Follow the import guide to load the files into "
            "your workspace. Keep the weekly review page open while you plan the next week. "
            "Personal use only."),
        "pages": [
            {"title": "Start Here", "markdown": "# Start Here\n\n" + (
                "This client CRM template keeps every lead, conversation, follow-up and project note "
                "in one calm workspace. Use the Clients database for contact details and the Projects "
                "database for active work. Add a new row for each person, choose a status, then update "
                "the next follow-up date after every conversation. The page is written for freelancers "
                "and small studios that want a simple, repeatable client process without extra tools. "
                "Keep the page open during calls so every promise, question and next step is written "
                "down before the conversation ends. At the end of the day, clear the inbox and add "
                "fresh notes to the right client row. When a project finishes, move it to the archive "
                "database and leave a short summary for the next review. ")},
            {"title": "Weekly Review", "markdown": "# Weekly Review\n\n" + (
                "Set aside twenty minutes at the end of each week to review every active client. "
                "Check the status column, confirm the next follow-up date, and write one short note "
                "about the next step. Move finished projects to the archive database and clear out "
                "old leads that are no longer relevant. This page gives you a repeatable weekly habit "
                "so no client message is forgotten and every project has a clear next action. Start "
                "with the oldest open lead, then review active projects, then check the calendar for "
                "the week ahead. Finish by choosing the three most important follow-ups for Monday "
                "morning and write them in the notes column of each client row. Keep this page open "
                "during the review and tick each step as you finish it. ")},
        ],
        "databases": [
            {"name": "Clients", "columns": ["Name", "Status", "Follow up", "Notes"],
             "rows": [["Client A", "Lead", "2026-01-10", "Discovery call booked"],
                      ["Client B", "Active", "2026-01-12", "Waiting for brief"],
                      ["Client C", "Past", "2025-12-20", "Project complete"],
                      ["Client D", "Lead", "2026-01-15", "Referred by Client B"],
                      ["Client E", "Active", "2026-01-18", "Draft review next"]]},
            {"name": "Projects", "columns": ["Project", "Status", "Due", "Owner"],
             "rows": [["Website refresh", "Active", "2026-02-01", "Client B"],
                      ["Brand guide", "Draft", "2026-01-30", "Client D"],
                      ["Newsletter", "Done", "2025-12-15", "Client A"],
                      ["Launch plan", "Active", "2026-02-10", "Client E"]]},
        ],
    }, "Notion Templates Agent"),
    "resume": ({
        "kind": "resume", "name": "ATS Resume Bundle", "short_name": "ats-resume",
        "cover_image_idea": "Resume on desk", "instructions": ["Open the files."],
        "listing": _listing(
            "ATS Resume and Cover Letter Bundle",
            "Includes a Word resume, a Word cover letter and an Excel job tracker with a Job "
            "Applications tab and automatic overdue formulas. Replace the placeholders with your own "
            "details. The single-column layout stays easy for applicant tracking systems to read. "
            "Personal use only."),
        "resume": {"sections": [
            {"heading": "Professional Summary", "bullets": ["[Two lines about you]"]},
            {"heading": "Experience", "bullets": ["[Job Title], [Company]"]},
        ]},
        "cover_letter": {"paragraphs": ["Dear [Hiring Manager Name],", "[Why this role]", "[Your Name]"]},
        "tracker": {
            "name": "Job Applications",
            "columns": [
                {"name": "Company", "type": "text", "options": ["Acme"]},
                {"name": "Role", "type": "text", "options": ["Designer"]},
                {"name": "Applied", "type": "date"},
                {"name": "Follow-up", "type": "date"},
                {"name": "Status", "type": "text", "options": ["Open", "Closed"]},
            ],
            "rows": [["[Company A]", "[Role]", "2026-01-05", "2026-01-12", "Open"]],
            "overdue": {"due": "Follow-up", "status": "Status", "done": "Closed"},
        },
    }, "Resume Bundle Agent"),
    "dated_planner": ({
        "kind": "dated_planner", "name": "2027 Dated Planner", "short_name": "planner-2027",
        "cover_image_idea": "Calendar preview", "span": "2027", "start_day": "Monday",
        "instructions": ["Print at 100%."],
        "listing": _listing("2027 Dated Planner Calendar"),
    }, "ADHD Planners Agent"),
}

CUSTOM_SPECS = {
    "event_planner": {
        "kind": "event_planner", "name": "Wedding Planner", "short_name": "wedding-planner",
        "title": "Wedding Planner", "event_type": "wedding", "edition": "indian",
        "cover_image_idea": "Wedding planning desk", "instructions": ["Print at 100%."],
        "listing": _listing("Wedding Planner Printable"),
    },
    "flashcards": {
        "kind": "flashcards", "name": "Alphabet Flashcards", "short_name": "alphabet-flashcards",
        "title": "Alphabet Flashcards", "topic": "alphabet", "cover_image_idea": "Alphabet cards",
        "instructions": ["Print and cut."],
        "listing": _listing("Alphabet Flashcards for Kids"),
    },
    "habit_tracker": {
        "kind": "habit_tracker", "name": "Habit Tracker", "short_name": "habit-tracker",
        "title": "Monthly Habit Tracker", "cover_image_idea": "Habit grid",
        "instructions": ["Print at 100%."],
        "listing": _listing("Monthly Habit Tracker"),
    },
    "recipe_book": {
        "kind": "recipe_book", "name": "Recipe Book", "short_name": "recipe-book",
        "title": "Everyday Recipe Book", "cover_image_idea": "Recipe cards",
        "instructions": ["Print at 100%."],
        "listing": _listing("Everyday Recipe Book and Meal Planner"),
    },
    "email_signature": {
        "kind": "email_signature", "name": "Email Signature Pack", "short_name": "email-signature",
        "title": "Email Signature Pack", "cover_image_idea": "Signature preview",
        "instructions": ["Copy the HTML."],
        "listing": _listing(
            "Email Signature Templates",
            "Includes six HTML email signature templates and six preview images. Each template uses "
            "inline styling, web-safe fonts and a compact width that works in common email programs. "
            "Replace the placeholders with your own name, role, phone, email and website. "
            "Personal use only."),
    },
    "study_planner": {
        "kind": "study_planner", "name": "Study Planner", "short_name": "study-planner",
        "title": "Exam Study Planner", "cover_image_idea": "Study desk",
        "instructions": ["Print at 100%."],
        "listing": _listing("Exam and Study Planner"),
    },
    "wall_art": {
        "kind": "wall_art", "name": "Wall Art Set", "short_name": "wall-art",
        "title": "Mindful Wall Art Set", "cover_image_idea": "Framed prints",
        "instructions": ["Print and frame."],
        "listing": _listing("Mindful Wall Art Print Set"),
    },
}


def format_spec(kind):
    if kind in BUILTIN_SPECS:
        raw, niche = BUILTIN_SPECS[kind]
        parsed, _reason = pb.parse_spec(json.dumps(copy.deepcopy(raw)), niche)
        return parsed or copy.deepcopy(raw)
    return copy.deepcopy(CUSTOM_SPECS[kind])


def _first_image(pack):
    for pattern in ("cover*.png", "cover*.jpg", "preview*.png", "art*.png", "*.png", "*.jpg"):
        for path in sorted(pack.glob(pattern)):
            if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg"}:
                return path
    return None


def _write_small_cover(source, cover):
    with Image.open(source) as image:
        image = image.convert("RGB")
        image.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
        image.save(cover, "PNG", compress_level=6)


def _stage_images(pack):
    cover = pack / "cover.png"
    if not cover.is_file():
        source = _first_image(pack)
        if source is not None:
            _write_small_cover(source, cover)
    if not any(pack.glob("preview*")):
        if cover.is_file():
            shutil.copyfile(cover, pack / "preview-1.png")
        else:
            source = _first_image(pack)
            if source is not None:
                _write_small_cover(source, pack / "preview-1.png")


def _buyer_files(kind, pack):
    if kind == "notion":
        return [path for path in pack.iterdir()
                if path.is_file() and "workspace-import.zip" in path.name]
    keep_names = {"README.md", "README.txt", "LICENSE.txt", "HOW_TO_IMPORT.md",
                  "README-HOW-TO-INSTALL.txt", "PRINTING.txt"}
    skip_names = {"product.zip", "listing.md", "listing.txt", "checklist.txt",
                  "spec.json", "listing.json", "product.json", "cover_image_idea.txt"}
    owner_prefixes = ("cover", "preview", "thumbnail", "pinterest", "mockup")
    files = []
    for path in sorted(pack.iterdir()):
        if not path.is_file() or path.name in skip_names or path.name.startswith(owner_prefixes):
            continue
        if path.suffix.lower() in {".png", ".jpg", ".jpeg"} and path.stat().st_size > 2_000_000:
            continue
        if path.name in keep_names or path.suffix.lower() in {
                ".pdf", ".xlsx", ".docx", ".zip", ".html", ".png", ".jpg", ".jpeg"}:
            files.append(path)
    return files


def _marketing_assets(marketing_root, pack):
    target = marketing_root / pack.name
    cover = pack / "cover.png"
    if not cover.is_file():
        cover = _first_image(pack)
    data = cover.read_bytes() if cover is not None else b"\x89PNG\r\n\x1a\n"
    for folder, name in (("carousel", "slide-1.png"), ("pins", "pin-1.png"),
                         ("reel", "frame-1.png"), ("covers", "cover-1.png")):
        (target / folder).mkdir(parents=True, exist_ok=True)
        (target / folder / name).write_bytes(data)
    (target / "blog.md").write_text("Blog", encoding="utf-8")
    (target / "LAUNCH.md").write_text("Launch", encoding="utf-8")
    return target


def build_pack(kind, root):
    products = root / "products"
    marketing_root = root / "marketing"
    products.mkdir(parents=True, exist_ok=True)
    marketing_root.mkdir(parents=True, exist_ok=True)
    approval_id = FORMAT_ORDER.index(kind) + 1
    folder_name = pb.build_product(format_spec(kind), approval_id, out_dir=str(products))
    pack = products / folder_name
    _stage_images(pack)
    (pack / "listing.txt").write_text(
        upload_pack._listing((pack / "listing.md").read_text(encoding="utf-8")), encoding="utf-8")
    (pack / "checklist.txt").write_text(
        upload_pack._checklist((pack / "cover.png").is_file()) + "Theme: Calm Sage\n", encoding="utf-8")
    with zipfile.ZipFile(pack / "product.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for path in _buyer_files(kind, pack):
            archive.write(path, arcname=path.name)
    store_kits.build(pack)
    gumroad_files = gumroad_images.for_pack(pack)
    marketing = _marketing_assets(marketing_root, pack)
    return {
        "kind": kind,
        "approval_id": approval_id,
        "pack": pack,
        "products": products,
        "marketing_root": marketing_root,
        "marketing": marketing,
        "gumroad_files": gumroad_files,
    }


class Recorder:
    def __init__(self, products, marketing):
        self.texts = []
        self.documents = []
        self.photos = []
        self.state = {}
        self.ctx = {
            "db_path": None,
            "chat_id": "12345",
            "products_dir": str(products),
            "marketing_dir": str(marketing),
            "send_text": self.send_text,
            "send_document": self.send_document,
            "send_photo": self.send_photo,
            "send_media_group": lambda paths, caption="": True,
            "state_get": lambda key, default=None: self.state.get(key, default),
            "state_set": lambda key, value: self.state.__setitem__(key, value),
        }

    def send_text(self, text, keyboard=None):
        self.texts.append(text)
        return True

    def send_document(self, path, caption=""):
        self.documents.append((path, caption))
        return True

    def send_photo(self, path, caption=""):
        self.photos.append((path, caption))
        return True

    @property
    def all_text(self):
        return "\n".join(self.texts)


@unittest.skipUnless(HAS_PIL and gumroad_images.available(), "needs Pillow")
class SellFlowMixin:
    KIND = None

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix=f"occ1_{cls.KIND}_")
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.built = build_pack(cls.KIND, Path(cls.tmp.name))
        cls.pack = cls.built["pack"]

    def test_1_pack_builds_with_expected_files(self):
        self.assertTrue((self.pack / "listing.md").is_file())
        self.assertTrue((self.pack / "listing.txt").is_file())
        self.assertTrue((self.pack / "checklist.txt").is_file())
        self.assertTrue((self.pack / "product.zip").is_file())
        self.assertIn(self.KIND, self.built["kind"])
        self.assertTrue(self.pack.name.startswith(f"{self.built['approval_id']}-"))

    def test_2_truth_check_runs_and_reports_built_kind(self):
        result = truth_check.check(str(self.pack))
        self.assertIn(result["kind"], {"pdf", "sheets", "notion", "resume", "unknown"})
        self.assertIsInstance(result["score"], int)
        self.assertIsInstance(result["problems"], list)
        if self.KIND in TRUTH_READY:
            self.assertEqual(result["verdict"], "ready", result["problems"])

    def test_3_listing_and_checklist_have_no_money_text(self):
        listing = (self.pack / "listing.txt").read_text(encoding="utf-8")
        checklist = (self.pack / "checklist.txt").read_text(encoding="utf-8")
        self.assertIsNone(sell_ready.check_money_text(listing))
        self.assertIsNone(sell_ready.check_money_text(checklist))
        self.assertNotIn("$", listing + checklist)
        self.assertNotIn("USD", listing + checklist)

    def test_4_listed_files_exist_and_product_zip_is_valid(self):
        with zipfile.ZipFile(self.pack / "product.zip") as archive:
            self.assertIsNone(archive.testzip())
            members = archive.namelist()
        self.assertTrue(members)
        self.assertTrue(any(Path(name).suffix.lower() in {
            ".pdf", ".xlsx", ".docx", ".zip", ".html", ".png"} for name in members))
        listing = (self.pack / "listing.txt").read_text(encoding="utf-8")
        section = listing.split("What you get:", 1)[1].split("Tags:", 1)[0]
        items = [line.strip() for line in section.splitlines() if line.strip()]
        self.assertTrue(items)
        for item in items:
            name = item.split(" — ", 1)[0].strip()
            self.assertTrue((self.pack / name).is_file() or any(
                Path(member).name == name for member in members), item)

    def test_5_gumroad_kit_is_copy_paste_ready(self):
        kit_path = self.pack / "store-kits" / "gumroad.txt"
        self.assertTrue(kit_path.is_file())
        kit = kit_path.read_text(encoding="utf-8")
        self.assertIn("=== GUMROAD LISTING KIT ===", kit)
        self.assertIn("Price: owner decides", kit)
        self.assertIn("CHECKLIST OF CLICKS", kit)
        self.assertIsNone(sell_ready.check_money_text(kit))

    def test_6_gumroad_images_have_exact_sizes(self):
        files = [Path(path) for path in self.built["gumroad_files"]]
        self.assertGreaterEqual(len(files), 6)
        self.assertEqual(files[0].name, "01-thumbnail-600x600.jpg")
        self.assertEqual(files[1].name, "02-cover-1280x720.jpg")
        self.assertTrue(all(path.is_file() for path in files))
        expected = [(600, 600), (1280, 720)] + [(1600, 1200)] * (len(files) - 2)
        for path, size in zip(files, expected):
            with Image.open(path) as image:
                self.assertEqual(image.size, size, path.name)
        self.assertTrue((files[0].parent / "gumroad-alt-text.json").is_file())

    def test_7_tg_sell_reaches_sellable_or_almost(self):
        recorder = Recorder(self.built["products"], self.built["marketing_root"])
        with mock.patch.object(telegram_inbox, "build_plugin_ctx", return_value=recorder.ctx):
            handled, reply = telegram_inbox.call_plugins(
                f"/sell {self.built['approval_id']}", token="1:test", chat_id=12345,
                db_path=None, plugins=[tg_sell])
        evaluation = sell_ready.evaluate_pack(self.pack, self.built["marketing_root"])
        self.assertIn(evaluation["status"], (sell_ready.STATUS_SELLABLE, sell_ready.STATUS_ALMOST),
                      evaluation["top_fixes"])
        self.assertTrue(handled, reply)
        self.assertIn("GUMROAD FIELDS", recorder.all_text)
        self.assertIn("GUMROAD CHECKLIST", recorder.all_text)
        self.assertGreaterEqual(len(recorder.documents), 1)
        self.assertGreaterEqual(len(recorder.photos), 1)
        self.assertIsNone(sell_ready.check_money_text(recorder.all_text))


class TestOcc1Sheets(SellFlowMixin, unittest.TestCase):
    KIND = "sheets"


class TestOcc1Planner(SellFlowMixin, unittest.TestCase):
    KIND = "planner"


class TestOcc1Notion(SellFlowMixin, unittest.TestCase):
    KIND = "notion"


class TestOcc1Resume(SellFlowMixin, unittest.TestCase):
    KIND = "resume"


class TestOcc1DatedPlanner(SellFlowMixin, unittest.TestCase):
    KIND = "dated_planner"


class TestOcc1EventPlanner(SellFlowMixin, unittest.TestCase):
    KIND = "event_planner"


class TestOcc1Flashcards(SellFlowMixin, unittest.TestCase):
    KIND = "flashcards"


class TestOcc1HabitTracker(SellFlowMixin, unittest.TestCase):
    KIND = "habit_tracker"


class TestOcc1RecipeBook(SellFlowMixin, unittest.TestCase):
    KIND = "recipe_book"


class TestOcc1EmailSignature(SellFlowMixin, unittest.TestCase):
    KIND = "email_signature"


class TestOcc1StudyPlanner(SellFlowMixin, unittest.TestCase):
    KIND = "study_planner"


class TestOcc1WallArt(SellFlowMixin, unittest.TestCase):
    KIND = "wall_art"


if __name__ == "__main__":
    unittest.main()
