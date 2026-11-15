"""PATH-C: every product type's REAL built files pass the Truth Checker honestly.

Each test builds the product with the real builder in a temp folder (never in the repo) and reads the
real PDF / HTML / PNG files back. The checkers (truth_check, sell_ready, product_qa_deep) are not changed
or patched: these tests prove the generators now make files that pass them.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import truth_check

try:
    import PIL  # noqa: F401
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


def build_folder(module_name, spec, tmp):
    """Build with the real format builder and return the folder (standard -A4 / -Letter names)."""
    import product_builder as pb
    folder = Path(tmp) / "pack"
    folder.mkdir(parents=True, exist_ok=True)
    module = __import__(module_name)
    module.build(dict(spec), str(folder))
    pb.standard_pdf_names(str(folder), spec)
    return folder


def page_problems(folder):
    return [p for p in truth_check.file_quality(truth_check.facts(str(folder)))]


def page_texts(folder, number, name_end="-A4.pdf"):
    pdf = next(p for p in sorted(Path(folder).glob("*.pdf")) if p.name.endswith(name_end))
    page = truth_check._pdf_pages(str(pdf))[number - 1]
    return [item[4] for item in page["items"]], page


@unittest.skipUnless(HAS_PIL, "needs Pillow")
class HabitTrackerTests(unittest.TestCase):
    SPEC = {"kind": "habit_tracker", "title": "Monthly Habit Tracker", "short_name": "habit-tracker"}

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ccc_habit_")
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.folder = build_folder("builder_habit", cls.SPEC, cls.tmp.name)

    def test_both_paper_sizes_have_standard_names(self):
        names = sorted(p.name for p in self.folder.glob("*.pdf"))
        self.assertEqual(names, ["habit-tracker-A4.pdf", "habit-tracker-Letter.pdf"])

    def test_no_empty_page_problems(self):
        self.assertEqual(page_problems(self.folder), [])

    def test_title_page_has_a_real_start_here_card(self):
        import builder_habit
        for end in ("-A4.pdf", "-Letter.pdf"):
            texts, page = page_texts(self.folder, 1, end)
            self.assertIn("Start here", texts)
            for label in builder_habit.START_HERE_FIELDS:
                self.assertIn(label, texts)
            for name, _note in builder_habit.PAGE_GUIDE:
                self.assertIn(name, texts)
            self.assertLess(page["low"], 0.35 * 842)

    def test_page_guide_names_real_pages(self):
        import builder_habit
        facts = truth_check.facts(str(self.folder))
        titles = " | ".join(facts["texts"])
        for name, _note in builder_habit.PAGE_GUIDE:
            self.assertIn(name, titles)

    def test_long_title_does_not_overlap_the_card(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec = dict(self.SPEC, title="A Very Long Monthly Habit And Mood Tracker Title For Busy People Who Plan")
            folder = build_folder("builder_habit", spec, tmp)
            _texts, page = page_texts(folder, 1)
            card_top = max(y for _f, _s, _x, y, t in page["items"] if t == "Start here")
            title_low = min(y for f, s, _x, y, _t in page["items"] if f == "F2" and s >= 16)
            self.assertGreater(title_low, card_top + 40)
            self.assertEqual(page_problems(folder), [])

    def test_truth_check_ready(self):
        verdict = truth_check.check(str(self.folder), with_images=False)
        self.assertFalse([p for p in verdict["problems"] if "empty" in p], verdict["problems"])


@unittest.skipUnless(HAS_PIL, "needs Pillow")
class RecipeBookTests(unittest.TestCase):
    SPEC = {"kind": "recipe_book", "title": "Everyday Recipe Book", "short_name": "recipe-book"}

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ccc_recipe_")
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.folder = build_folder("builder_recipe", cls.SPEC, cls.tmp.name)

    def test_both_paper_sizes_have_standard_names(self):
        names = sorted(p.name for p in self.folder.glob("*.pdf"))
        self.assertEqual(names, ["recipe-book-A4.pdf", "recipe-book-Letter.pdf"])

    def test_no_empty_page_problems(self):
        self.assertEqual(page_problems(self.folder), [])

    def test_title_page_indexes_every_recipe_card(self):
        import builder_recipe
        norm = builder_recipe.normalize_spec(self.SPEC)
        for end in ("-A4.pdf", "-Letter.pdf"):
            texts, page = page_texts(self.folder, 1, end)
            self.assertIn("Recipe index", texts)
            for number, recipe in enumerate(norm["recipes"], 1):
                self.assertIn(f"{number:02d}", texts)
                self.assertIn(recipe["title"], texts)
                self.assertIn(recipe["prep"], texts)
            self.assertLess(page["low"], 0.35 * 842)

    def test_index_names_match_the_cards(self):
        facts = truth_check.facts(str(self.folder))
        card_titles = set(facts["headings"])
        import builder_recipe
        for recipe in builder_recipe.normalize_spec(self.SPEC)["recipes"]:
            self.assertIn(recipe["title"], card_titles)

    def test_twelve_recipes_still_fit_the_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            recipes = [{"title": f"Recipe Number {n}", "category": "Mains"} for n in range(1, 13)]
            folder = build_folder("builder_recipe", dict(self.SPEC, recipes=recipes), tmp)
            texts, _page = page_texts(folder, 1)
            self.assertIn("Recipe Number 12", texts)
            self.assertEqual(page_problems(folder), [])


@unittest.skipUnless(HAS_PIL, "needs Pillow")
class StudyPlannerTests(unittest.TestCase):
    SPEC = {"kind": "study_planner", "title": "Exam Study Planner", "short_name": "study-planner"}

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ccc_study_")
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.folder = build_folder("builder_study", cls.SPEC, cls.tmp.name)

    def test_both_paper_sizes_have_standard_names(self):
        names = sorted(p.name for p in self.folder.glob("*.pdf"))
        self.assertEqual(names, ["study-planner-A4.pdf", "study-planner-Letter.pdf"])

    def test_no_empty_page_problems(self):
        self.assertEqual(page_problems(self.folder), [])

    def test_title_page_has_an_exam_plan_per_subject(self):
        import builder_study
        norm = builder_study.normalize_spec(self.SPEC)
        for end in ("-A4.pdf", "-Letter.pdf"):
            texts, page = page_texts(self.folder, 1, end)
            self.assertIn("My exam plan", texts)
            for label in builder_study.PLAN_FIELDS:
                self.assertIn(label, texts)
            for subject in norm["subjects"]:
                self.assertIn(subject, texts)
            self.assertIn(str(norm["chapters_per_subject"]), texts)
            self.assertGreaterEqual(page["drawn"], 10 * 5)        # rows x (4 cells + confidence boxes)
            self.assertLess(page["low"], 0.35 * 842)

    def test_mock_log_rows_reach_the_bottom_margin(self):
        for end, height in (("-A4.pdf", 842), ("-Letter.pdf", 792)):
            texts, page = page_texts(self.folder, 4, end)
            self.assertIn("Mock-Test Log", texts)
            self.assertLess(page["low"], 0.35 * height)
            self.assertGreaterEqual(page["drawn"], 5 * 16)        # at least 16 rows of 5 cells

    def test_exam_label_is_written_properly(self):
        import builder_study
        self.assertEqual(builder_study.normalize_spec({"exam": "jee"})["exam_label"], "JEE")
        self.assertEqual(builder_study.normalize_spec({"exam": "10th"})["exam_label"], "Class 10")
        texts, _page = page_texts(self.folder, 1)
        self.assertIn("JEE preparation set", texts)

    def test_many_subjects_still_fit(self):
        with tempfile.TemporaryDirectory() as tmp:
            subjects = [f"Subject {n}" for n in range(1, 13)]
            folder = build_folder("builder_study", dict(self.SPEC, subjects=subjects), tmp)
            texts, _page = page_texts(folder, 1)
            self.assertIn("Subject 12", texts)
            self.assertEqual(page_problems(folder), [])


def stream_ops(folder, number, name_end="-A4.pdf"):
    """The raw drawing operators of one PDF page (to check fills, not only outlines)."""
    import re
    pdf = next(p for p in sorted(Path(folder).glob("*.pdf")) if p.name.endswith(name_end))
    data = pdf.read_bytes().decode("latin-1")
    return re.findall(r"stream\n(.*?)\nendstream", data, re.S)[number - 1]


@unittest.skipUnless(HAS_PIL, "needs Pillow")
class EventPlannerTests(unittest.TestCase):
    SPEC = {"kind": "event_planner", "title": "Wedding Planner", "short_name": "wedding-planner",
            "event_type": "wedding", "edition": "indian"}

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ccc_event_")
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.folder = build_folder("builder_eventplanner", cls.SPEC, cls.tmp.name)

    def test_no_empty_page_problems_in_either_size(self):
        self.assertEqual(page_problems(self.folder), [])
        for end, height in (("-A4.pdf", 842), ("-Letter.pdf", 792)):
            pdf = next(p for p in self.folder.glob("*.pdf") if p.name.endswith(end))
            for number, page in enumerate(truth_check._pdf_pages(str(pdf)), 1):
                self.assertLess(page["low"], 0.35 * height, f"{end} page {number}")

    def test_countdown_has_writing_lines_for_every_window(self):
        import builder_eventplanner
        texts, page = page_texts(self.folder, 3)
        milestones = builder_eventplanner.compute_countdown_milestones(None)
        for m in milestones:
            self.assertIn(m["window"], texts)
        # one checkbox per milestone plus at least one tick box + line per milestone for own tasks
        self.assertGreaterEqual(page["drawn"], len(milestones) * 4)

    def test_schedule_has_blank_rows_to_the_margin(self):
        texts, page = page_texts(self.folder, 9)
        self.assertIn("06:00 AM", texts)
        self.assertIn("11:30 PM", texts)
        self.assertGreaterEqual(texts.count("__:__"), 5)

    def test_table_headers_are_filled_so_white_names_show(self):
        ops = stream_ops(self.folder, 9)
        texts, _page = page_texts(self.folder, 9)
        for name in ("Time Slot", "Activity / Program Segment", "Location / Area", "Lead Person / Coordinator"):
            self.assertIn(name, texts)
        self.assertRegex(ops, r"re f")                     # a real fill, not only an outline

    def test_long_header_title_moves_subtitle_up(self):
        texts, page = page_texts(self.folder, 10)
        sub = [y for _f, _s, _x, y, t in page["items"] if t == "Pre-Wedding Traditions"]
        title = [y for _f, _s, _x, y, t in page["items"] if t.startswith("Indian Wedding Celebrations")]
        self.assertTrue(sub and title)
        self.assertGreater(sub[0], title[0] + 10)

    def test_ceremony_cards_have_notes_and_no_edge_text(self):
        texts, page = page_texts(self.folder, 10)
        self.assertEqual(texts.count("Notes:"), 3)
        width = 595.28
        for _font, size, x, _y, text in page["items"]:
            self.assertLessEqual(x + len(text) * size * 0.5, width - 14.17, text)


@unittest.skipUnless(HAS_PIL, "needs Pillow")
class FlashcardsTests(unittest.TestCase):
    SPEC = {"kind": "flashcards", "title": "Alphabet Flashcards", "short_name": "alphabet-flashcards",
            "topic": "alphabet"}

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ccc_cards_")
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.folder = build_folder("builder_flashcards", cls.SPEC, cls.tmp.name)

    def test_real_deck_in_both_sizes_and_cover_does_not_take_the_main_name(self):
        names = sorted(p.name for p in self.folder.glob("*.pdf"))
        self.assertEqual(names, ["alphabet-flashcards-A4.pdf", "alphabet-flashcards-Letter.pdf",
                                 "alphabet-flashcards-cover-A4.pdf"])
        facts = truth_check.facts(str(self.folder))
        self.assertEqual(facts["pages"], 9)                       # how-to page + 4 sheets x (fronts + backs)
        self.assertEqual(page_problems(self.folder), [])

    def test_every_card_has_front_and_back_text(self):
        import builder_flashcards
        cards = builder_flashcards.deck_cards("alphabet")
        self.assertEqual(len(cards), 26)
        all_texts = []
        for number in range(2, 10):
            texts, _page = page_texts(self.folder, number)
            all_texts += texts
        for card in cards:
            self.assertIn(card["front"], all_texts)
            self.assertIn(card["back"], all_texts)
            self.assertIn(card["prompt"], all_texts)

    def test_fronts_then_mirrored_backs(self):
        texts2, _p = page_texts(self.folder, 2)
        texts3, page3 = page_texts(self.folder, 3)
        self.assertIn("Fronts · sheet 1 of 4", texts2)
        self.assertIn("Backs · sheet 1 of 4", texts3)
        x_of = {t: x for _f, _s, x, _y, t in page3["items"]}
        self.assertGreater(x_of["Ant"], x_of["Ball"])      # card 1 is left on the front, right on the back

    def test_cut_lines_are_dashed_on_every_sheet(self):
        for number in range(2, 10):
            self.assertIn("[5 4] 0 d", stream_ops(self.folder, number))

    def test_how_to_page_lists_every_card_and_reaches_the_bottom(self):
        texts, page = page_texts(self.folder, 1)
        self.assertIn("How to print and cut", texts)
        self.assertIn("Practice log", texts)
        self.assertTrue(any(t.startswith("26  Z z") for t in texts))
        self.assertLess(page["low"], 0.35 * 842)

    def test_png_sheets_and_previews_are_real_and_big_enough(self):
        from PIL import Image
        for name in ("preview_1.png", "preview_2.png", "preview_3.png"):
            with Image.open(self.folder / name) as image:
                self.assertGreaterEqual(min(image.size), 720)
                self.assertGreater(len(image.getcolors(1 << 20) or []), 2)    # not a blank picture
        with Image.open(self.folder / "flashcards_alphabet_page_1.png") as image:
            self.assertEqual(image.size, (2480, 3508))

    def test_every_topic_builds_a_clean_deck(self):
        import builder_flashcards
        for topic, per in (("numbers", 8), ("shapes", 12), ("colours", 8), ("telugu", 12)):
            with self.subTest(topic=topic), tempfile.TemporaryDirectory() as tmp:
                builder_flashcards.build({"kind": "flashcards", "topic": topic, "cards_per_page": per,
                                          "short_name": f"{topic}-cards"}, tmp)
                os.remove(os.path.join(tmp, "cover.pdf"))
                facts = truth_check.facts(tmp)
                self.assertGreaterEqual(facts["pages"], 3)
                self.assertEqual(truth_check.file_quality(facts), [])

    def test_shape_points_stay_inside_their_box(self):
        import builder_flashcards
        for shape in builder_flashcards.SHAPES:
            points = builder_flashcards.shape_points(shape, 100, 100, 80)
            self.assertGreaterEqual(len(points), 3)
            for x, y in points:
                self.assertLessEqual(abs(x - 100), 41)
                self.assertLessEqual(abs(y - 100), 41)

    def test_listing_page_count_ignores_the_box_label(self):
        import product_builder as pb
        facts = pb._delivery_facts(str(self.folder))
        self.assertEqual(facts["pages"], 9)
        self.assertIn("9 printable pages in every PDF", facts["details"])


@unittest.skipUnless(HAS_PIL, "needs Pillow")
class EmailSignatureTests(unittest.TestCase):
    SPEC = {"kind": "email_signature", "title": "Email Signature Pack", "short_name": "email-signature"}

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ccc_sig_")
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.folder = build_folder("builder_signature", cls.SPEC, cls.tmp.name)

    def test_full_pack_has_a_printable_guide_in_both_sizes(self):
        names = sorted(p.name for p in self.folder.glob("*.pdf"))
        self.assertEqual(names, ["email-signature-install-guide-A4.pdf", "email-signature-install-guide-Letter.pdf"])
        facts = truth_check.facts(str(self.folder))
        self.assertEqual(facts["pages"], 7)
        self.assertEqual(page_problems(self.folder), [])

    def test_guide_covers_every_template_placeholder_and_email_program(self):
        import builder_signature as bs
        texts = []
        for number in range(1, 8):
            page_items, _page = page_texts(self.folder, number)
            texts += page_items
        joined = " | ".join(texts)
        for tdef in bs.TEMPLATE_DEFS:
            self.assertIn(tdef["name"], joined)
            self.assertIn(tdef["filename"], joined)
        for placeholder, _meaning in bs.PLACEHOLDER_HELP:
            self.assertIn(placeholder, joined)
        for client, steps, _fixes in bs.GUIDE_CLIENTS:
            self.assertIn(f"Set up in {client}", joined)

    def test_guide_is_written_for_buyers_in_english(self):
        texts, _page = page_texts(self.folder, 5)
        joined = " ".join(texts)
        for tenglish in ("cheyandi", "lo open", "mee "):
            self.assertNotIn(tenglish, joined)

    def test_signature_pictures_are_buyer_files_not_store_previews(self):
        import builder_signature as bs
        for tdef in bs.TEMPLATE_DEFS:
            self.assertFalse(tdef["preview"].startswith("preview"))
            self.assertTrue((self.folder / tdef["preview"]).is_file())

    def test_bare_template_build_keeps_its_13_files(self):
        import builder_signature as bs
        with tempfile.TemporaryDirectory() as tmp:
            manifest = bs.build({}, tmp)
            self.assertEqual(len(os.listdir(tmp)), 13)
            self.assertEqual(manifest["guides"], [])
            self.assertEqual(len(manifest["files"]), 13)

    def test_truth_check_proves_enough_with_a_listing(self):
        import product_builder as pb
        with tempfile.TemporaryDirectory() as tmp:
            spec = dict(self.SPEC, name="Email Signature Pack", cover_image_idea="Signature",
                        instructions=["Copy the HTML."],
                        listing={"title": "Email Signature Templates", "buyer": "adults", "platform": "Gumroad",
                                 "tags": ["email signature"], "description": "Six HTML email signatures."})
            folder = Path(tmp) / pb.build_product(spec, 7, out_dir=tmp)
            listing = (folder / "listing.md").read_text(encoding="utf-8")
            for tdef in __import__("builder_signature").TEMPLATE_DEFS:
                self.assertIn(f"`{tdef['filename']}` — HTML file", listing)
                self.assertIn(f"`{tdef['preview']}` — PNG image, 600 x 240 px", listing)
            verdict = truth_check.check(str(folder), with_images=False)
            self.assertEqual(verdict["verdict"], "ready", verdict["problems"])
            self.assertGreaterEqual(len(verdict["proven"]), truth_check.EXTRA_PROOF_PROMISES)


@unittest.skipUnless(HAS_PIL, "needs Pillow")
class WallArtTests(unittest.TestCase):
    SPEC = {"kind": "wall_art", "title": "Mindful Wall Art Set", "short_name": "wall-art"}

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ccc_wall_")
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.folder = build_folder("builder_wallart", cls.SPEC, cls.tmp.name)

    def test_pack_has_vector_pdfs_in_a4_and_letter(self):
        names = sorted(p.name for p in self.folder.glob("*.pdf"))
        self.assertEqual(names, ["wall-art-A4.pdf", "wall-art-Letter.pdf"])
        facts = truth_check.facts(str(self.folder))
        self.assertEqual(facts["pages"], 3)
        self.assertEqual(page_problems(self.folder), [])

    def test_every_page_is_full_bleed_art_with_the_quote(self):
        import builder_wallart as bw
        _title, designs, palette, _key = bw.normalize_spec(self.SPEC)
        for end, (w, h) in (("-A4.pdf", (595.3, 841.9)), ("-Letter.pdf", (612.0, 792.0))):
            for number, design in enumerate(designs, 1):
                texts, page = page_texts(self.folder, number, end)
                joined = " ".join(texts)
                for word in design["quote"].upper().split():
                    self.assertIn(word, joined)
                self.assertIn(f"ART PRINT NO. {number:02d} / {len(designs):02d}", joined)
                self.assertIn(f"0.0 0.0 {w:.1f} {h:.1f} re f", stream_ops(self.folder, number, end))
                self.assertGreaterEqual(page["drawn"], 4)

    def test_deep_qa_passes_honestly(self):
        import product_qa_deep
        result = product_qa_deep.check_folder(str(self.folder))
        self.assertGreaterEqual(result["score"], 60, result["problems"])
        self.assertFalse([p for p in result["problems"] if p["kind"] == "blank_page"])

    def test_all_png_sizes_still_there(self):
        import builder_wallart as bw
        for design in range(1, 4):
            for size in bw.PRINT_SIZES:
                self.assertTrue((self.folder / f"art_{design:02d}_{size}.png").is_file())

    def test_printing_guide_names_the_real_pdfs(self):
        text = (self.folder / "PRINTING.txt").read_text(encoding="utf-8")
        self.assertIn("wall-art-A4.pdf and wall-art-Letter.pdf", text)
        self.assertNotIn("Combined PDF", text)

    def test_bare_build_keeps_the_old_17_files(self):
        import builder_wallart as bw
        with tempfile.TemporaryDirectory() as tmp:
            result = bw.build({"title": "Bare", "count": 3}, tmp)
            self.assertEqual(result["total_files"], 17)
            self.assertTrue(os.path.isfile(os.path.join(tmp, "wall_art_set_a4_collection.pdf")))

    def test_every_motif_and_six_designs_stay_clean(self):
        import builder_wallart as bw
        with tempfile.TemporaryDirectory() as tmp:
            bw.build(dict(self.SPEC, count=6, palette="sage", theme="modern_focus"), tmp)
            facts = truth_check.facts(tmp)
            self.assertEqual(facts["pages"], 6)
            self.assertEqual(truth_check.file_quality(facts), [])

    def test_readme_lists_file_names_not_paths(self):
        import product_builder as pb
        with tempfile.TemporaryDirectory() as tmp:
            spec = dict(self.SPEC, name="Wall Art", cover_image_idea="Prints", instructions=["Print."],
                        listing={"title": "Wall Art", "buyer": "adults", "platform": "Gumroad",
                                 "tags": ["wall art"], "description": "Three prints."})
            folder = Path(tmp) / pb.build_product(spec, 9, out_dir=tmp)
            readme = (folder / "README.md").read_text(encoding="utf-8")
            self.assertIn("- `wall-art-A4.pdf`", readme)
            self.assertNotIn(tmp, readme)


if __name__ == "__main__":
    unittest.main()
