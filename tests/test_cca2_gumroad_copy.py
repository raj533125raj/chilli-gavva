"""CCA-2: Gumroad copy that converts — built only from what is really in the pack.

Every one of the 12 product types gets a temp pack with real files in product.zip. The listing of
each pack also carries three bullets the files do NOT back up (a file type the pack lacks, a design
tool template and a video); the kit must leave them out. No network, no model, temp folders only.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import store_kits

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PDF = b"%PDF-1.4 sample\n%%EOF"
BRANDS = store_kits.GUMROAD_BRAND_WORDS

# type -> (title, listing tags, files inside product.zip, a file type the pack does NOT have)
TYPES = {
    "planner": ("ADHD Daily Focus Planner Printable", "focus planner, daily planner, Canva template, adhd planner",
                ["focus-planner-a4.pdf", "focus-planner-letter.pdf", "README.md", "LICENSE.txt"], "Excel spreadsheet version"),
    "dated_planner": ("2027 Dated Calendar Planner", "2027 planner, dated planner, monthly calendar",
                      ["2027-planner.pdf", "README.md"], "Word document version"),
    "sheets": ("Budget Tracker Spreadsheet", "budget tracker, Excel template, expense tracker, Google Sheets",
               ["budget-tracker.xlsx", "README.md", "LICENSE.txt"], "Printable PDF version"),
    "notion": ("Notion Student Dashboard Template", "notion template, student dashboard, Notion",
               ["notion-dashboard.csv", "notion-pages.md", "HOW_TO_IMPORT.md"], "Printable PDF version"),
    "resume": ("ATS Resume Template Word", "resume template, cv template, Microsoft Word resume",
               ["resume.docx", "cover-letter.docx", "README.md"], "Excel spreadsheet tracker"),
    "habit_tracker": ("Habit Tracker Printable 30 Day", "habit tracker, 30 day tracker, routine",
                      ["habit-tracker.pdf", "LICENSE.txt"], "CSV data export"),
    "study_planner": ("Study Planner For Exams", "study planner, exam planner, student",
                      ["study-planner.pdf", "README.md"], "Word document version"),
    "recipe_book": ("Recipe Book Template", "recipe book, recipe template, kitchen",
                    ["recipe-book.pdf"], "Excel spreadsheet version"),
    "flashcards": ("Spanish Flashcards Printable", "flashcards, study cards, language learning",
                   ["flashcards.pdf", "README.md"], "PNG image files"),
    "event_planner": ("Wedding Event Planner Checklist", "event planner, wedding checklist, party planner",
                      ["event-planner.pdf", "guest-list.xlsx", "README.md"], "Word document version"),
    "email_signature": ("Email Signature Template", "email signature, signature template, Gmail signature",
                        ["signature.html", "signature-preview.png", "INSTALL.txt"], "Printable PDF version"),
    "wall_art": ("Minimal Quote Wall Art Print Set", "wall art, printable art, IKEA frame size, quote print",
                 ["art_01_a4.png", "art_02_a4.png", "wall_art_set.pdf", "PRINTING.txt"], "Excel spreadsheet version"),
}
TYPES.update({
    # CC-BIG-1: money-sheets family and ADHD planner editions
    **{kind: (title, tags, [f"{kind}.xlsx", f"{kind}-csv-tabs.zip", "README.txt", "LICENSE.txt"], "Printable PDF version")
       for kind, title, tags in (
           ("debt_payoff", "Debt Payoff Tracker Spreadsheet", "debt payoff tracker, debt snowball, budget spreadsheet"),
           ("couples_budget", "Couples Budget Spreadsheet", "couples budget, shared budget, budget spreadsheet"),
           ("freelance_budget", "Freelancer Budget Spreadsheet", "freelancer budget, tax set aside, budget spreadsheet"),
           ("sinking_funds", "Sinking Funds Tracker Spreadsheet", "sinking funds, savings goals, budget spreadsheet"))},
    "adhd_planner": ("Energy-Based Daily Planner Printable", "adhd planner, printable planner, undated planner",
                     ["adhd_planner_a4.pdf", "adhd_planner_letter.pdf", "README.txt", "LICENSE.txt"],
                     "Excel spreadsheet version"),
    "digital_planner": ("Digital Planner With Month Tabs", "digital planner, tablet planner, month tabs",
        ["digital_planner.pdf", "README.txt", "LICENSE.txt"], "Excel spreadsheet version"),
    "biz_sheets": ("Small Business Spreadsheet Workbook", "small business spreadsheet, order tracker spreadsheet, inventory spreadsheet",
        ["biz_sheets.xlsx", "Orders.csv", "README.txt", "LICENSE.txt"], "Printable PDF version"),
    'teacher_planner': ('Teacher Planner Printable', 'teacher planner, lesson planner, printable',
        ['teacher_planner_a4.pdf', 'teacher_planner_letter.pdf', "README.txt"], "Word document version"),
    'fitness_planner': ('Fitness Planner Printable', 'fitness planner, workout planner, printable',
        ['fitness_planner_a4.pdf', 'fitness_planner_letter.pdf', "README.txt"], "Word document version"),
    'baby_tracker': ('Baby Tracker Printable', 'baby tracker, baby log, printable',
        ['baby_tracker_a4.pdf', 'baby_tracker_letter.pdf', "README.txt"], "Word document version"),
    'small_business_log': ('Small Business Log Printable', 'small business log, business tracker, printable',
        ['small_business_log_a4.pdf', 'small_business_log_letter.pdf', "README.txt"], "Word document version"),
    'kids_chore_reward': ('Kids Chore And Reward Chart Printable', 'kids chore chart, reward chart, printable',
        ['kids_chore_reward_a4.pdf', 'kids_chore_reward_letter.pdf', "README.txt"], "Word document version"),
})

DESCRIPTION = ("A calm, simple set of pages made to keep things clear. Open the files, fill them in and "
               "keep going at your own pace. Made for everyday use at home or at work.")


class Base(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()

    def make_pack(self, product_type="planner", name=None, title=None, tags=None, files=None, description=DESCRIPTION,
                  what_you_get=None, extra_files=()):
        default_title, default_tags, default_files, missing = TYPES[product_type]
        pack = self.root / (name or f"1-{product_type.replace('_', '-')}")
        pack.mkdir(parents=True)
        members = list(default_files if files is None else files)
        with zipfile.ZipFile(pack / "product.zip", "w") as bundle:
            for member in members:
                data = PDF if member.endswith(".pdf") else PNG if member.endswith(".png") else b"sample content"
                bundle.writestr(member, data)
        bullets = what_you_get if what_you_get is not None else [
            "Clear layout that is easy to read", missing, "Bonus Canva template", "Video walkthrough"]
        (pack / "listing.txt").write_text(
            f"Title: {title or default_title}\n\nDescription:\n{description}\n\nTags: {tags or default_tags}\n\n"
            "What you get:\n" + "\n".join(f"- {line}" for line in bullets) + "\n", encoding="utf-8")
        (pack / "checklist.txt").write_text("- [ ] upload\n", encoding="utf-8")
        (pack / "cover.png").write_bytes(PNG)
        (pack / "preview-1.png").write_bytes(PNG)
        for extra, data in extra_files:
            target = pack / extra
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        return pack

    def info(self, pack):
        return store_kits.parse_listing_file(pack / "listing.txt")

    def kit(self, pack):
        return store_kits.generate_kit_text("gumroad", pack, self.info(pack))

    def copy(self, pack):
        return store_kits.gumroad_copy(pack, self.info(pack))

    def description(self, pack):
        kit = self.kit(pack)
        return kit.split("--- FIELD: LONG DESCRIPTION ---\nDescription:\n", 1)[1].split("\n(Length:", 1)[0]

    def block(self, pack, heading):
        text = self.description(pack)
        return text.split(heading + "\n", 1)[1].split("\n\n", 1)[0]

    def tags(self, pack):
        kit = self.kit(pack)
        return re.search(r"^Tags \(\d+ tags\):\n(.*)$", kit, re.MULTILINE).group(1).split(", ")


class EveryProductTypeTests(Base):
    """Filled below: seven checks for each registered product type."""

    def check_type_is_detected(self, product_type):
        self.assertEqual(self.copy(self.make_pack(product_type))["product_type"], product_type)

    def check_hook_is_the_first_line(self, product_type):
        pack = self.make_pack(product_type)
        first_line = self.description(pack).splitlines()[0]
        self.assertEqual(first_line, "A calm, simple set of pages made to keep things clear.")
        self.assertLessEqual(len(first_line), store_kits.GUMROAD_HOOK_LIMIT)
        self.assertEqual(self.description(pack).splitlines()[1], "")

    def check_unbacked_promises_are_left_out(self, product_type):
        pack = self.make_pack(product_type)
        inside = self.block(pack, "WHAT'S INSIDE")
        self.assertIn("- Clear layout that is easy to read", inside)
        self.assertNotIn(TYPES[product_type][3], inside)
        self.assertNotIn("Canva", inside)
        self.assertNotIn("Video", inside)

    def check_whats_inside_matches_the_real_files(self, product_type):
        pack = self.make_pack(product_type)
        members = [name.lower() for name in TYPES[product_type][2]]
        inside = self.block(pack, "WHAT'S INSIDE").lower()
        for word, suffixes in (("pdf", (".pdf",)), ("spreadsheet workbook", (".xlsx",)), (".docx", (".docx",)),
                               ("image file", (".png", ".jpg")), ("html", (".html",)), ("csv", (".csv",))):
            has = any(name.endswith(suffixes) for name in members)
            self.assertEqual(word in inside, has, f"{word!r} in What's inside but files are {members}")

    def check_file_list_is_exactly_the_zip(self, product_type):
        pack = self.make_pack(product_type)
        listed = [line[2:] for line in self.block(pack, "FILES YOU GET").splitlines()]
        self.assertEqual(sorted(listed, key=str.lower), sorted(TYPES[product_type][2], key=str.lower))
        for owner_file in ("listing.txt", "checklist.txt", "cover.png", "preview-1.png", "product.zip"):
            self.assertNotIn(owner_file, listed)

    def check_ten_brand_free_tags(self, product_type):
        tags = self.tags(self.make_pack(product_type))
        self.assertEqual(len(tags), 10)
        self.assertEqual(len(set(tags)), 10)
        for tag in tags:
            self.assertEqual(tag, tag.lower())
            self.assertLessEqual(len(tag), 30)
            self.assertFalse(set(re.findall(r"[a-z0-9-]+", tag)) & BRANDS, tag)

    def check_how_to_use_fits_the_files_and_price_is_the_owners(self, product_type):
        pack = self.make_pack(product_type)
        kit = self.kit(pack)
        members = [name.lower() for name in TYPES[product_type][2]]
        steps = self.block(pack, "HOW TO USE").lower()
        self.assertTrue(steps.startswith("1. download the files"))
        for word, suffix in (("pdf", ".pdf"), (".xlsx", ".xlsx"), (".docx", ".docx"), (".html", ".html"),
                             (".csv", ".csv"), ("image files", ".png")):
            self.assertEqual(word in steps, any(name.endswith(suffix) for name in members), f"{word!r}: {steps}")
        self.assertIn("Price: owner decides", kit)
        self.assertIsNone(store_kits.check_money(kit))
        self.assertIn("WHO IT IS FOR\n- " + store_kits.GUMROAD_TYPES[product_type]["audience"][0], kit)


def _attach():
    checks = [name for name in dir(EveryProductTypeTests) if name.startswith("check_")]
    for product_type in TYPES:
        for check in checks:
            def test(self, product_type=product_type, check=check):
                getattr(self, check)(product_type)
            test.__name__ = f"test_{product_type}_{check[len('check_'):]}"
            setattr(EveryProductTypeTests, test.__name__, test)


_attach()


class TypeTableTests(unittest.TestCase):
    def test_all_registered_formats_have_a_profile(self):
        import format_registry
        format_registry.discover_builders()
        self.assertEqual(sorted(store_kits.GUMROAD_PRODUCT_TYPES), sorted(format_registry.list_registered_formats()))

    def test_this_test_file_covers_all_registered_formats(self):
        self.assertEqual(sorted(TYPES), sorted(store_kits.GUMROAD_PRODUCT_TYPES))

    def test_every_profile_is_complete_and_clean(self):
        for key, profile in store_kits.GUMROAD_TYPES.items():
            with self.subTest(key=key):
                self.assertTrue(profile["label"])
                self.assertEqual(len(profile["audience"]), 2)
                self.assertGreaterEqual(len(profile["tags"]), 8)
                for tag in profile["tags"]:
                    self.assertLessEqual(len(tag), 30)
                    self.assertFalse(set(tag.split()) & BRANDS, tag)
                text = " ".join([profile["label"]] + profile["audience"] + profile["tags"])
                self.assertIsNone(store_kits.check_money(text))
                self.assertIsNone(re.search(r"guarantee|income|\bearn|\bcure|lose weight|get hired|best ?seller", text, re.I))


class RealFilesTests(Base):
    def test_zip_members_are_the_buyer_files(self):
        pack = self.make_pack("planner")
        names = [item["name"] for item in store_kits.gumroad_pack_files(pack)]
        self.assertEqual(names, ["focus-planner-a4.pdf", "focus-planner-letter.pdf", "LICENSE.txt", "README.md"])

    def test_owner_notes_cover_and_previews_are_not_buyer_files(self):
        pack = self.make_pack("planner")
        names = {item["name"] for item in store_kits.gumroad_pack_files(pack)}
        self.assertFalse(names & {"listing.txt", "checklist.txt", "cover.png", "preview-1.png", "product.zip"})

    def test_helper_folders_are_not_buyer_files(self):
        pack = self.make_pack("planner", extra_files=[("store-kits/gumroad.txt", b"kit"), ("support/FAQ.md", b"# FAQ"),
                                                       ("gumroad/thumbnail.png", PNG)])
        names = {item["name"] for item in store_kits.gumroad_pack_files(pack)}
        self.assertFalse(names & {"gumroad.txt", "FAQ.md", "thumbnail.png"})

    def test_deliverable_next_to_the_zip_is_listed(self):
        pack = self.make_pack("planner", extra_files=[("bonus-page.pdf", PDF)])
        files = {item["name"]: item["where"] for item in store_kits.gumroad_pack_files(pack)}
        self.assertEqual(files["bonus-page.pdf"], "folder")
        self.assertEqual(files["README.md"], "zip")

    def test_broken_zip_gives_no_members_and_no_crash(self):
        pack = self.make_pack("planner")
        (pack / "product.zip").write_bytes(b"not a zip")
        self.assertEqual(store_kits.gumroad_pack_files(pack), [])
        self.assertIn("=== GUMROAD LISTING KIT ===", self.kit(pack))

    def test_pack_without_zip_still_gets_a_kit_without_file_claims(self):
        pack = self.make_pack("planner")
        (pack / "product.zip").unlink()
        text = self.description(pack)
        self.assertNotIn("FILES YOU GET", text)
        self.assertNotIn("printable PDF", text)
        self.assertNotIn("Unzip", text)

    def test_mac_junk_in_the_zip_is_ignored(self):
        pack = self.make_pack("planner", files=["planner.pdf", "__MACOSX/._planner.pdf", ".DS_Store"])
        self.assertEqual([item["name"] for item in store_kits.gumroad_pack_files(pack)], ["planner.pdf"])

    def test_many_files_are_cut_with_a_count(self):
        pack = self.make_pack("wall_art", files=[f"art_{index:02d}.png" for index in range(1, 21)])
        lines = self.block(pack, "FILES YOU GET").splitlines()
        self.assertEqual(len(lines), 16)
        self.assertEqual(lines[-1], "- and 5 more file(s)")

    def test_pdf_count_and_paper_sizes_come_from_the_file_names(self):
        self.assertIn("- 2 printable PDF files (A4 and US Letter sizes)", self.block(self.make_pack("planner"), "WHAT'S INSIDE"))

    def test_one_pdf_without_size_names_says_nothing_about_paper_size(self):
        inside = self.block(self.make_pack("habit_tracker"), "WHAT'S INSIDE")
        self.assertIn("- 1 printable PDF file", inside)
        self.assertNotIn("A4", inside)

    def test_fillable_is_only_said_for_a_real_fillable_pdf(self):
        plain = self.make_pack("habit_tracker")
        self.assertNotIn("fillable", self.description(plain).lower())
        fillable = self.make_pack("study_planner", what_you_get=["Fillable pages you can type into"])
        with zipfile.ZipFile(fillable / "product.zip", "w") as bundle:
            bundle.writestr("study-planner.pdf", b"%PDF-1.4\n/AcroForm << >>\n%%EOF")
        inside = self.block(fillable, "WHAT'S INSIDE")
        self.assertIn("- Fillable pages you can type into", inside)
        self.assertIn("- 1 fillable PDF file", inside)

    def test_fillable_promise_is_dropped_for_a_plain_pdf(self):
        pack = self.make_pack("habit_tracker", what_you_get=["Fillable pages you can type into", "Thirty day grid"])
        inside = self.block(pack, "WHAT'S INSIDE")
        self.assertNotIn("Fillable", inside)
        self.assertIn("- Thirty day grid", inside)

    def test_a4_and_letter_promises_need_such_files(self):
        pack = self.make_pack("habit_tracker", what_you_get=["A4 PDF file", "US Letter PDF file", "Monthly grid"])
        inside = self.block(pack, "WHAT'S INSIDE")
        self.assertNotIn("A4 PDF file", inside)
        self.assertNotIn("US Letter", inside)
        backed = self.make_pack("planner", what_you_get=["A4 PDF file", "US Letter PDF file"])
        self.assertIn("- A4 PDF file\n- US Letter PDF file", self.block(backed, "WHAT'S INSIDE"))

    def test_never_shipped_things_are_always_dropped(self):
        for line in ("Bonus Canva template", "Video walkthrough", "Free updates for life", "Lifetime updates",
                     "Printed copy shipped to you", "Private community access", "MP3 audio guide", "24/7 support"):
            with self.subTest(line=line):
                self.assertFalse(store_kits._claim_is_backed(line, {".pdf"}, ["a.pdf"]))

    def test_plain_content_bullets_are_kept(self):
        for line in ("Daily priority blocks", "Weekly review page", "Notes section with wide margins"):
            with self.subTest(line=line):
                self.assertTrue(store_kits._claim_is_backed(line, {".pdf"}, ["a.pdf"]))

    def test_bullets_with_money_are_dropped(self):
        pack = self.make_pack("planner", what_you_get=["Daily page"])
        info = self.info(pack)
        info["what_you_get"] = "- Daily page\n- Worth 20 USD of value"
        self.assertEqual(store_kits.gumroad_whats_inside(pack, info)[0], "Daily page")
        self.assertFalse(any("USD" in line for line in store_kits.gumroad_whats_inside(pack, info)))

    def test_duplicate_bullets_appear_once(self):
        pack = self.make_pack("planner", what_you_get=["Readme guide", "readme guide", "Personal use licence"])
        inside = self.block(pack, "WHAT'S INSIDE").lower()
        self.assertEqual(inside.count("readme guide"), 1)
        self.assertEqual(len(re.findall(r"licen[cs]e", inside)), 1)
        self.assertNotIn("read-me guide to get you started", inside)


class TypeDetectionTests(Base):
    def test_listing_words_are_not_trusted_without_matching_files(self):
        pack = self.make_pack("sheets", files=["tracker.pdf"])            # says spreadsheet, ships a PDF
        self.assertEqual(self.copy(pack)["product_type"], "planner")
        self.assertNotIn("spreadsheet workbook", self.description(pack))

    def test_unknown_title_falls_back_to_the_files(self):
        for files, expected in ((["a.xlsx"], "sheets"), (["a.docx"], "resume"), (["a.pdf"], "planner"),
                                (["a.csv", "b.md"], "notion"), (["a.png"], "wall_art"), (["notes.txt"], "digital_pack")):
            with self.subTest(files=files):
                pack = self.make_pack("planner", name=f"9-{expected}-{len(files)}", title="Zxqv Bundle", tags="zxqv",
                                      files=files)
                self.assertEqual(self.copy(pack)["product_type"], expected)

    def test_empty_pack_is_a_plain_digital_download(self):
        pack = self.make_pack("planner", title="Zxqv Bundle", tags="zxqv", files=[])
        copy = self.copy(pack)
        self.assertEqual(copy["product_type"], "digital_pack")
        self.assertEqual(copy["files"], [])


class HookTests(Base):
    def test_listing_first_sentence_is_the_hook(self):
        hook, rest = store_kits.gumroad_hook("T", "Plan your week on one calm page. Print it. Use it.", "planner")
        self.assertEqual(hook, "Plan your week on one calm page.")
        self.assertEqual(rest, "Print it. Use it.")

    def test_too_short_first_sentence_gets_a_factual_hook(self):
        hook, rest = store_kits.gumroad_hook("Focus Planner", "Nice. Very nice planner pages for you.", "planner")
        self.assertEqual(hook, "Focus Planner — a printable planner you can download and use today.")
        self.assertEqual(rest, "Nice. Very nice planner pages for you.")

    def test_too_long_first_sentence_gets_a_factual_hook(self):
        long_sentence = "This planner " + "really " * 40 + "helps."
        hook, rest = store_kits.gumroad_hook("Focus Planner", long_sentence, "planner")
        self.assertLessEqual(len(hook), store_kits.GUMROAD_HOOK_LIMIT)
        self.assertTrue(hook.startswith("Focus Planner — a printable planner"))
        self.assertEqual(rest, long_sentence)

    def test_empty_description_still_has_a_hook(self):
        pack = self.make_pack("sheets")
        info = dict(self.info(pack), description="")
        kit = store_kits.generate_kit_text("gumroad", pack, info)
        self.assertIn("Description:\nBudget Tracker Spreadsheet — a spreadsheet you can download and use today.\n\n"
                      "WHAT'S INSIDE", kit)

    def test_hook_for_every_type_names_the_type_plainly(self):
        for product_type, profile in store_kits.GUMROAD_TYPES.items():
            with self.subTest(product_type=product_type):
                hook, _ = store_kits.gumroad_hook("My Pack", "", product_type)
                self.assertIn(profile["label"], hook)
                self.assertIsNone(re.search(r"guarantee|\bbest\b|#1|income|\bearn", hook, re.I))

    def test_very_long_title_hook_is_cut_at_a_word(self):
        hook, _ = store_kits.gumroad_hook("Planner " * 40, "", "planner")
        self.assertLessEqual(len(hook), store_kits.GUMROAD_HOOK_LIMIT)
        self.assertFalse(hook.endswith(" "))

    def test_short_description_field_is_filled_when_the_listing_has_none(self):
        pack = self.make_pack("sheets")
        kit = store_kits.generate_kit_text("gumroad", pack, dict(self.info(pack), description=""))
        self.assertIn("Short Description: Budget Tracker Spreadsheet — a spreadsheet you can download", kit)


class TagTests(unittest.TestCase):
    def test_listing_tags_come_first(self):
        tags = store_kits.gumroad_tags("focus planner, adhd planner", "planner")
        self.assertEqual(tags[:2], ["focus planner", "adhd planner"])
        self.assertEqual(len(tags), 10)

    def test_brand_tags_are_removed(self):
        tags = store_kits.gumroad_tags("Canva template, Notion dashboard, Etsy bestseller, GoodNotes planner, "
                                       "Google Sheets budget, Microsoft Excel, focus planner", "planner")
        self.assertEqual(tags[0], "focus planner")
        for tag in tags:
            self.assertFalse(set(tag.split()) & BRANDS)

    def test_duplicates_and_case(self):
        tags = store_kits.gumroad_tags("Planner, planner, PLANNER, Daily Planner", "planner")
        self.assertEqual(tags.count("planner"), 1)
        self.assertEqual(tags.count("daily planner"), 1)

    def test_more_than_ten_listing_tags_are_cut_to_ten(self):
        tags = store_kits.gumroad_tags(", ".join(f"tag {index}" for index in range(20)), "planner")
        self.assertEqual(tags, [f"tag {index}" for index in range(10)])

    def test_money_and_overlong_tags_are_dropped(self):
        tags = store_kits.gumroad_tags("under $5 planner, " + "x" * 40 + ", focus planner", "planner")
        self.assertEqual(tags[0], "focus planner")
        self.assertFalse(any("$" in tag or len(tag) > 30 for tag in tags))

    def test_empty_listing_tags_still_give_ten_for_every_type(self):
        for product_type in store_kits.GUMROAD_TYPES:
            with self.subTest(product_type=product_type):
                tags = store_kits.gumroad_tags("", product_type)
                self.assertEqual(len(tags), 10)
                self.assertEqual(len(set(tags)), 10)

    def test_hashes_and_quotes_are_cleaned(self):
        self.assertEqual(store_kits.gumroad_tags('#Planner, "weekly"', "planner")[:2], ["planner", "weekly"])

    def test_unknown_type_uses_the_fallback_profile(self):
        self.assertEqual(len(store_kits.gumroad_tags("", "not-a-type")), 10)


class FaqTests(Base):
    def test_faq_from_the_support_kit_file_when_present(self):
        faq = ("# Buyer FAQ\n\n## What is in this pack?\nThis pack contains 3 file(s).\n\n"
               "## Can I print it at home?\nYes, on any home printer.\n\n## Is it editable?\nThe PDF is print-ready.\n")
        pack = self.make_pack("planner", extra_files=[("support/FAQ.md", faq.encode())])
        self.assertEqual(self.copy(pack)["faq"], [("Can I print it at home?", "Yes, on any home printer."),
                                                  ("Is it editable?", "The PDF is print-ready.")])
        self.assertIn("FAQ\nQ: Can I print it at home?\nA: Yes, on any home printer.", self.description(pack))

    def test_faq_without_support_file_is_worked_out_from_the_real_files(self):
        pack = self.make_pack("sheets")
        answers = dict(self.copy(pack)["faq"])
        self.assertIn("Excel or another app that opens .xlsx files", answers["What software do I need?"])
        self.assertNotIn("PDF reader", answers["What software do I need?"])

    def test_faq_never_points_at_files_the_buyer_does_not_get(self):
        for product_type in TYPES:
            with self.subTest(product_type=product_type):
                pack = self.make_pack(product_type)
                real = {name.lower() for name in TYPES[product_type][2]}
                for _question, answer in self.copy(pack)["faq"]:
                    for named in re.findall(r"\b[\w-]+\.(?:md|txt|pdf|xlsx|csv|docx|png|html)\b", answer):
                        self.assertIn(named.lower(), real)

    def test_at_most_five_questions(self):
        self.assertLessEqual(len(self.copy(self.make_pack("planner"))["faq"]), 5)

    def test_money_in_a_faq_answer_is_skipped(self):
        faq = "## How much?\nIt costs 9 USD.\n\n## Can I print it?\nYes.\n"
        pack = self.make_pack("planner", extra_files=[("support/FAQ.md", faq.encode())])
        self.assertEqual(self.copy(pack)["faq"], [("Can I print it?", "Yes.")])

    def test_unreadable_faq_file_falls_back(self):
        pack = self.make_pack("planner", extra_files=[("support/FAQ.md", b"\xff\xfe\x00bad")])
        self.assertTrue(self.copy(pack)["faq"])

    def test_only_real_files_helper(self):
        files = [{"name": "README.md"}]
        self.assertEqual(store_kits._only_real_files("Open README.md first. Then read HOW-TO-PRINT.md. Read it twice.", files),
                         "Open README.md first.")


class KitShapeTests(Base):
    def test_sections_come_in_selling_order(self):
        text = self.description(self.make_pack("planner"))
        order = [text.index(heading) for heading in ("WHAT'S INSIDE", "WHO IT IS FOR", "HOW TO USE", "FILES YOU GET", "FAQ\n")]
        self.assertEqual(order, sorted(order))
        self.assertTrue(text.endswith("Digital download. Nothing is shipped."))

    def test_rest_of_the_listing_description_follows_the_hook(self):
        text = self.description(self.make_pack("planner"))
        self.assertIn("Open the files, fill them in and keep going at your own pace. Made for everyday use", text)

    def test_field_headers_are_unchanged_for_tg_sell(self):
        kit = self.kit(self.make_pack("planner"))
        for header in ("--- FIELD: TITLE ---", "--- FIELD: SHORT DESCRIPTION ---", "--- FIELD: LONG DESCRIPTION ---",
                       "--- FIELD: TAGS ---", "--- FIELD: CATEGORY ---", "--- FIELD: PRICING ---",
                       "--- FIELD: FILES TO UPLOAD (IN ORDER) ---", "--- FIELD: IMAGES TO UPLOAD (IN ORDER) ---"):
            self.assertIn(header, kit)

    def test_description_stays_under_the_gumroad_limit(self):
        pack = self.make_pack("planner", description="Clear pages for calm days. " + "More words here. " * 900)
        text = self.description(pack)
        self.assertLessEqual(store_kits.count_graphemes(text), store_kits.STORE_LIMITS["gumroad"]["desc_limit"])

    def test_other_stores_keep_the_plain_listing_text(self):
        pack = self.make_pack("planner")
        for store in ("payhip", "etsy", "kofi", "topmate", "instamojo"):
            if store not in store_kits.STORE_LIMITS:
                continue
            with self.subTest(store=store):
                kit = store_kits.generate_kit_text(store, pack, self.info(pack))
                self.assertNotIn("WHO IT IS FOR", kit)
                self.assertNotIn("HOW TO USE", kit)

    def test_build_writes_the_same_gumroad_text_to_the_kit_file(self):
        pack = self.make_pack("planner")
        written = store_kits.build(pack)["gumroad"].read_text(encoding="utf-8")
        self.assertIn("WHAT'S INSIDE", written)
        self.assertIn("Price: owner decides", written)

    def test_same_pack_gives_the_same_kit_twice(self):
        pack = self.make_pack("event_planner")
        self.assertEqual(self.kit(pack), self.kit(pack))

    def test_no_income_health_or_job_promises_in_generated_text(self):
        for product_type in TYPES:
            with self.subTest(product_type=product_type):
                text = self.description(self.make_pack(product_type))
                self.assertIsNone(re.search(r"guarantee|make money|passive income|get hired|lose weight|cure|"
                                            r"best ?seller|#1|limited time", text, re.I))

    def test_tg_sell_fields_carry_the_new_copy(self):
        import tg_sell
        fields = tg_sell.gumroad_fields(self.make_pack("planner"))
        self.assertIn("WHAT'S INSIDE", fields)
        self.assertIn("Price: owner decides", fields)
        self.assertNotIn("FILES TO UPLOAD", fields)


if __name__ == "__main__":
    unittest.main()
