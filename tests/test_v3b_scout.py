"""Unit tests for BINNY-V3B: SCOUT ideas that are different.

Covers:
1. Audience rotation list & presence of all 12 key audiences.
2. 7-day no-repeat window for audiences.
3. Max 2 ideas per audience per week cap.
4. Least-recently-used fallback when all audiences have been used.
5. Format filtering (allowed formats accepted, excluded formats rejected unless builder_<format>.py exists).
6. Dynamic discovery of builder_<format>.py modules.
7. Word shingles (k=5) and Jaccard similarity calculation.
8. 60-day draft similarity check rejecting items with similarity > 0.45.
9. Grounded 'why this sells' rationale generation per audience without currency/money amounts.
10. Preservation of existing scout signatures and workflows.
"""

import datetime
import json
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

import guardrails
import niche_topics
import scout


class TestV3BScoutAudienceRotation(unittest.TestCase):
    """Test suite covering audience rotation, detection, and tracking."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()
        scout.connect(self.db_path).close()

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_01_audience_list_contains_required_audiences(self):
        required = [
            "teachers",
            "students",
            "exam takers",
            "small shop owners",
            "home bakers",
            "fitness beginners",
            "new parents",
            "freelancers",
            "job seekers",
            "wedding planners",
            "Telugu-speaking families",
            "retirees",
        ]
        for req in required:
            self.assertIn(req, scout.AUDIENCES)

    def test_02_detect_audience_teachers(self):
        self.assertEqual(scout.detect_audience("Printable Planner for Teachers"), "teachers")
        self.assertEqual(scout.detect_audience("Lesson Plan for High School Teacher"), "teachers")
        self.assertEqual(scout.detect_audience("Classroom Educator Grading Sheet"), "teachers")

    def test_03_detect_audience_students(self):
        self.assertEqual(scout.detect_audience("Semester Study Guide for Students"), "students")
        self.assertEqual(scout.detect_audience("College Coursework Tracker Sheet"), "students")

    def test_04_detect_audience_exam_takers(self):
        self.assertEqual(scout.detect_audience("Revision Timetable for Exam Takers"), "exam takers")
        self.assertEqual(scout.detect_audience("Standardized Test Prep Checklist"), "exam takers")

    def test_05_detect_audience_small_shop_owners(self):
        self.assertEqual(scout.detect_audience("Daily Cash Log for Small Shop Owners"), "small shop owners")
        self.assertEqual(scout.detect_audience("Retail Store Inventory Worksheet"), "small shop owners")

    def test_06_detect_audience_home_bakers(self):
        self.assertEqual(scout.detect_audience("Custom Cake Order Log for Home Bakers"), "home bakers")
        self.assertEqual(scout.detect_audience("Bakery Recipe Batching Workbook"), "home bakers")

    def test_07_detect_audience_fitness_beginners(self):
        self.assertEqual(scout.detect_audience("Workout Log for Fitness Beginners"), "fitness beginners")
        self.assertEqual(scout.detect_audience("Starter Gym Exercise Planner"), "fitness beginners")

    def test_08_detect_audience_new_parents(self):
        self.assertEqual(scout.detect_audience("Sleep Schedule Tracker for New Parents"), "new parents")
        self.assertEqual(scout.detect_audience("Infant Feeding Log Checklist"), "new parents")
        self.assertEqual(scout.detect_audience("Baby Milestones Printable"), "new parents")

    def test_09_detect_audience_freelancers(self):
        self.assertEqual(scout.detect_audience("Invoice and Hours Sheet for Freelancers"), "freelancers")
        self.assertEqual(scout.detect_audience("Client Project Milestone Tracker"), "freelancers")

    def test_10_detect_audience_job_seekers(self):
        self.assertEqual(scout.detect_audience("Clean Resume Pack for Job Seekers"), "job seekers")
        self.assertEqual(scout.detect_audience("Career Application Tracking Sheet"), "job seekers")

    def test_11_detect_audience_wedding_planners(self):
        self.assertEqual(scout.detect_audience("Budget and Seating Checklist for Wedding Planners"), "wedding planners")
        self.assertEqual(scout.detect_audience("Ceremony Vendor Timeline Planner"), "wedding planners")

    def test_12_detect_audience_telugu_families(self):
        self.assertEqual(scout.detect_audience("Festival Calendar for Telugu-speaking Families"), "Telugu-speaking families")
        self.assertEqual(scout.detect_audience("Traditional Telugu Household Routine"), "Telugu-speaking families")

    def test_13_detect_audience_retirees(self):
        self.assertEqual(scout.detect_audience("Daily Activity Workbook for Retirees"), "retirees")
        self.assertEqual(scout.detect_audience("Senior Wellness and Garden Planner"), "retirees")

    def test_14_detect_audience_unknown_returns_none(self):
        self.assertIsNone(scout.detect_audience("Abstract Geometric Spiral Pattern"))

    def test_15_detect_audience_empty_returns_none(self):
        self.assertIsNone(scout.detect_audience(""))
        self.assertIsNone(scout.detect_audience(None))


class TestV3BScoutNoRepeatWindow(unittest.TestCase):
    """Test suite covering 7-day no-repeat window and weekly audience caps."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()
        scout.connect(self.db_path).close()

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_16_clean_db_has_all_audiences_available(self):
        avail = scout.get_available_audiences(db_path=self.db_path)
        self.assertEqual(len(avail), len(scout.AUDIENCES))
        for aud in scout.AUDIENCES:
            self.assertIn(aud, avail)

    def test_17_audience_used_3_days_ago_is_excluded_from_available(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        ts_3d = (now - datetime.timedelta(days=3)).isoformat()
        conn = scout.connect(self.db_path)
        conn.execute(
            "INSERT INTO scout_ideas (ts, category, idea, total_score, audience, status) "
            "VALUES (?, 'Printables', 'Lesson Plan for Teachers', 80, 'teachers', 'pending')",
            (ts_3d,)
        )
        conn.commit()
        conn.close()

        avail = scout.get_available_audiences(db_path=self.db_path)
        self.assertNotIn("teachers", avail)
        self.assertEqual(len(avail), len(scout.AUDIENCES) - 1)

    def test_18_audience_used_8_days_ago_is_available_again(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        ts_8d = (now - datetime.timedelta(days=8)).isoformat()
        conn = scout.connect(self.db_path)
        conn.execute(
            "INSERT INTO scout_ideas (ts, category, idea, total_score, audience, status) "
            "VALUES (?, 'Printables', 'Lesson Plan for Teachers', 80, 'teachers', 'pending')",
            (ts_8d,)
        )
        conn.commit()
        conn.close()

        avail = scout.get_available_audiences(db_path=self.db_path)
        self.assertIn("teachers", avail)
        self.assertEqual(len(avail), len(scout.AUDIENCES))

    def test_19_audience_in_approvals_table_tracked_in_history(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        ts_2d = (now - datetime.timedelta(days=2)).isoformat()
        conn = guardrails.connect(self.db_path)
        conn.execute(
            "INSERT INTO approvals (ts, kind, title, payload, status) "
            "VALUES (?, 'public_draft', 'Cake Order Sheet for Home Bakers', 'payload', 'pending')",
            (ts_2d,)
        )
        conn.commit()
        conn.close()

        avail = scout.get_available_audiences(db_path=self.db_path)
        self.assertNotIn("home bakers", avail)

    def test_20_weekly_cap_max_two_ideas_per_audience(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        ts_1d = (now - datetime.timedelta(days=1)).isoformat()
        ts_2d = (now - datetime.timedelta(days=2)).isoformat()

        conn = scout.connect(self.db_path)
        conn.execute("INSERT INTO scout_ideas (ts, idea, audience) VALUES (?, 'Idea 1', 'teachers')", (ts_1d,))
        conn.execute("INSERT INTO scout_ideas (ts, idea, audience) VALUES (?, 'Idea 2', 'teachers')", (ts_2d,))
        conn.commit()
        conn.close()

        counts = scout.get_audience_weekly_counts(db_path=self.db_path)
        self.assertEqual(counts["teachers"], 2)

        # Even when all audiences have been used and fallback to LRU happens,
        # an audience at cap (>= 2) should NOT be available.
        # Mark all other audiences with 1 usage
        conn = scout.connect(self.db_path)
        for aud in scout.AUDIENCES:
            if aud != "teachers":
                conn.execute("INSERT INTO scout_ideas (ts, idea, audience) VALUES (?, 'Idea x', ?)", (ts_1d, aud))
        conn.commit()
        conn.close()

        avail = scout.get_available_audiences(db_path=self.db_path)
        self.assertNotIn("teachers", avail)
        for aud in scout.AUDIENCES:
            if aud != "teachers":
                self.assertIn(aud, avail)

    def test_21_least_recently_used_fallback_when_all_used(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        conn = scout.connect(self.db_path)
        # Give each audience 1 usage at different days ago
        for idx, aud in enumerate(scout.AUDIENCES):
            ts = (now - datetime.timedelta(days=6 - (idx % 6))).isoformat()
            conn.execute("INSERT INTO scout_ideas (ts, idea, audience) VALUES (?, 'Idea', ?)", (ts, aud))
        conn.commit()
        conn.close()

        avail = scout.get_available_audiences(db_path=self.db_path)
        # All have 1 usage (< 2), so all are returned ordered by LRU
        self.assertEqual(len(avail), len(scout.AUDIENCES))


class TestV3BScoutFormatFilter(unittest.TestCase):
    """Test suite covering builder format support and filtering."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()
        scout.connect(self.db_path).close()

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_22_allowed_formats_constant(self):
        expected = (
            "printable planner",
            "tracker sheet",
            "workbook",
            "checklist",
            "worksheet set",
            "budget sheet",
            "resume pack",
        )
        self.assertEqual(scout.ALLOWED_FORMATS, expected)

    def test_23_is_builder_format_supported_for_all_allowed(self):
        for fmt in scout.ALLOWED_FORMATS:
            self.assertTrue(scout.is_builder_format_supported(fmt))

    def test_24_is_builder_format_supported_case_insensitive(self):
        self.assertTrue(scout.is_builder_format_supported("Printable Planner"))
        self.assertTrue(scout.is_builder_format_supported("TRACKER SHEET"))
        self.assertTrue(scout.is_builder_format_supported("Resume Pack"))

    def test_25_wall_art_is_supported_once_builder_wallart_exists(self):
        # V4B added builder_wallart.py, so wall art is no longer blocked.
        self.assertTrue(scout.is_builder_format_supported("wall art"))
        self.assertTrue(scout.is_builder_format_supported("boho wall art"))

    def test_26_slides_are_excluded_by_default(self):
        self.assertFalse(scout.is_builder_format_supported("slides"))
        self.assertFalse(scout.is_builder_format_supported("presentation slides"))

    def test_27_email_signature_is_supported_once_builder_signature_exists(self):
        # V4C added builder_signature.py, so email signatures are no longer blocked.
        self.assertTrue(scout.is_builder_format_supported("email signature"))
        self.assertTrue(scout.is_builder_format_supported("HTML email signature"))

    def test_28_dynamic_builder_module_enables_format(self):
        fake_module = os.path.join(os.path.dirname(os.path.abspath(scout.__file__)), "builder_slides.py")
        try:
            with open(fake_module, "w") as f:
                f.write("# fake builder\n")
            self.assertTrue(scout.is_builder_format_supported("slides"))
            self.assertIn("slides", scout.get_supported_formats())
        finally:
            if os.path.exists(fake_module):
                os.remove(fake_module)

        # Once file is removed, it becomes unsupported again
        self.assertFalse(scout.is_builder_format_supported("slides"))

    def test_29_detect_format_allowed(self):
        self.assertEqual(scout.detect_format("Weekly Workout Tracker Sheet"), "tracker sheet")
        self.assertEqual(scout.detect_format("Semester Revision Worksheet Set"), "worksheet set")
        self.assertEqual(scout.detect_format("Small Business Monthly Budget Sheet"), "budget sheet")
        self.assertEqual(scout.detect_format("Comprehensive Event Checklist"), "checklist")
        self.assertEqual(scout.detect_format("Professional Tech Resume Pack"), "resume pack")
        self.assertEqual(scout.detect_format("Mindfulness Daily Workbook"), "workbook")
        self.assertEqual(scout.detect_format("Classroom Teacher Printable Planner"), "printable planner")

    def test_30_detect_format_excluded(self):
        self.assertEqual(scout.detect_format("Minimalist Botanical Wall Art"), "wall art")
        self.assertEqual(scout.detect_format("Pitch Deck Presentation Slides"), "slides")
        self.assertEqual(scout.detect_format("Corporate Executive Email Signature"), "email signature")

    def test_31_detect_format_empty_returns_none(self):
        self.assertIsNone(scout.detect_format(""))
        self.assertIsNone(scout.detect_format(None))

    def test_32_get_supported_formats_returns_list(self):
        formats = scout.get_supported_formats()
        self.assertIsInstance(formats, list)
        self.assertTrue(len(formats) >= 7)


class TestV3BScoutSimilarityCheck(unittest.TestCase):
    """Test suite covering word shingles, Jaccard similarity, and 60-day draft deduplication."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()
        scout.connect(self.db_path).close()

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_33_tokenize_words_removes_punctuation_and_lowercases(self):
        text = "Hello, World! This is a [Draft] TEST 123."
        words = scout.tokenize_words(text)
        self.assertEqual(words, ["hello", "world", "this", "is", "a", "draft", "test", "123"])

    def test_34_tokenize_words_empty_returns_empty_list(self):
        self.assertEqual(scout.tokenize_words(""), [])
        self.assertEqual(scout.tokenize_words(None), [])

    def test_35_get_word_shingles_k5(self):
        words = ["one", "two", "three", "four", "five", "six", "seven"]
        shingles = scout.get_word_shingles(words, k=5)
        self.assertEqual(len(shingles), 3)
        self.assertIn("one two three four five", shingles)
        self.assertIn("two three four five six", shingles)
        self.assertIn("three four five six seven", shingles)

    def test_36_get_word_shingles_fewer_than_k_returns_single_shingle(self):
        words = ["one", "two", "three"]
        shingles = scout.get_word_shingles(words, k=5)
        self.assertEqual(shingles, {"one two three"})

    def test_37_get_word_shingles_empty_returns_empty_set(self):
        self.assertEqual(scout.get_word_shingles([], k=5), set())

    def test_38_jaccard_similarity_identical_sets(self):
        s = {"a b c", "d e f"}
        self.assertEqual(scout.jaccard_similarity(s, s), 1.0)

    def test_39_jaccard_similarity_disjoint_sets(self):
        s1 = {"a b c"}
        s2 = {"d e f"}
        self.assertEqual(scout.jaccard_similarity(s1, s2), 0.0)

    def test_40_jaccard_similarity_empty_sets(self):
        self.assertEqual(scout.jaccard_similarity(set(), {"a"}), 0.0)
        self.assertEqual(scout.jaccard_similarity({"a"}, set()), 0.0)
        self.assertEqual(scout.jaccard_similarity(set(), set()), 0.0)

    def test_41_compute_shingle_similarity_identical_titles(self):
        t1 = "Habit and Routine Worksheet Set for Students"
        t2 = "Habit and Routine Worksheet Set for Students"
        self.assertEqual(scout.compute_shingle_similarity(t1, t2), 1.0)

    def test_42_compute_shingle_similarity_completely_different_titles(self):
        t1 = "Habit and Routine Worksheet Set for Students"
        t2 = "Monthly Expense Budget Sheet for Small Shop Owners"
        sim = scout.compute_shingle_similarity(t1, t2)
        self.assertEqual(sim, 0.0)

    def test_43_is_similar_to_recent_drafts_rejects_over_threshold(self):
        drafts = [
            "Habit and Routine Printable Worksheet Set for Students"
        ]
        candidate = "Habit and Routine Printable Worksheet Set for Young Students"
        is_sim, sim_val, match = scout.is_similar_to_recent_drafts(candidate, drafts, threshold=0.45)
        self.assertTrue(is_sim)
        self.assertGreater(sim_val, 0.45)
        self.assertEqual(match, drafts[0])

    def test_44_is_similar_to_recent_drafts_accepts_distinct_title(self):
        drafts = [
            "Comprehensive Lesson Planning Printable Planner for Elementary Teachers"
        ]
        candidate = "Weekly Order Tracking Sheet for Home Bakers"
        is_sim, sim_val, _ = scout.is_similar_to_recent_drafts(candidate, drafts, threshold=0.45)
        self.assertFalse(is_sim)
        self.assertLessEqual(sim_val, 0.45)

    def test_45_get_recent_drafts_reads_approvals_within_60_days(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        ts_45d = (now - datetime.timedelta(days=45)).isoformat()
        ts_65d = (now - datetime.timedelta(days=65)).isoformat()

        conn = guardrails.connect(self.db_path)
        conn.execute("INSERT INTO approvals (ts, kind, title, payload) VALUES (?, 'public_draft', 'Draft 45d', 'p')", (ts_45d,))
        conn.execute("INSERT INTO approvals (ts, kind, title, payload) VALUES (?, 'public_draft', 'Draft 65d', 'p')", (ts_65d,))
        conn.commit()
        conn.close()

        recent = scout.get_recent_drafts(db_path=self.db_path, days=60)
        self.assertIn("Draft 45d", recent)
        self.assertNotIn("Draft 65d", recent)


class TestV3BScoutWhySells(unittest.TestCase):
    """Test suite covering audience-grounded 'why this sells' rationale generation."""

    def test_46_why_sells_grounded_in_teachers(self):
        line = scout.generate_why_sells("Lesson Planner", audience="teachers")
        self.assertIn("Teachers", line)
        self.assertIn("classroom", line.lower())

    def test_47_why_sells_grounded_in_students(self):
        line = scout.generate_why_sells("Revision Guide", audience="students")
        self.assertIn("Students", line)
        self.assertIn("exam", line.lower())

    def test_48_why_sells_grounded_in_exam_takers(self):
        line = scout.generate_why_sells("Syllabus Checklist", audience="exam takers")
        self.assertIn("Exam", line)

    def test_49_why_sells_grounded_in_small_shop_owners(self):
        line = scout.generate_why_sells("Cash Log", audience="small shop owners")
        self.assertIn("shopkeeper", line.lower())

    def test_50_why_sells_grounded_in_home_bakers(self):
        line = scout.generate_why_sells("Cake Orders", audience="home bakers")
        self.assertIn("bakers", line.lower())
        self.assertIn("recipe", line.lower())

    def test_51_why_sells_grounded_in_fitness_beginners(self):
        line = scout.generate_why_sells("Workout Log", audience="fitness beginners")
        self.assertIn("Beginners", line)
        self.assertIn("workout", line.lower())

    def test_52_why_sells_grounded_in_new_parents(self):
        line = scout.generate_why_sells("Feeding Schedule", audience="new parents")
        self.assertIn("caregivers", line.lower())
        self.assertIn("infant", line.lower())

    def test_53_why_sells_grounded_in_freelancers(self):
        line = scout.generate_why_sells("Invoice Tracker", audience="freelancers")
        self.assertIn("contractors", line.lower())

    def test_54_why_sells_grounded_in_job_seekers(self):
        line = scout.generate_why_sells("Resume Pack", audience="job seekers")
        self.assertIn("career", line.lower())

    def test_55_why_sells_grounded_in_wedding_planners(self):
        line = scout.generate_why_sells("Vendor Timeline", audience="wedding planners")
        self.assertIn("couples", line.lower())
        self.assertIn("vendor", line.lower())

    def test_56_why_sells_grounded_in_telugu_families(self):
        line = scout.generate_why_sells("Festival Guide", audience="Telugu-speaking families")
        self.assertIn("Telugu", line)
        self.assertIn("festival", line.lower())

    def test_57_why_sells_grounded_in_retirees(self):
        line = scout.generate_why_sells("Daily Routine", audience="retirees")
        self.assertIn("Seniors", line)

    def test_58_why_sells_never_contains_money_or_currency(self):
        forbidden = ["$", "USD", "EUR", "GBP", "rupee", "price", "income", "profit", "passive income", "earn money"]
        for aud in scout.AUDIENCES:
            line = scout.generate_why_sells("Test Idea", audience=aud)
            for f in forbidden:
                self.assertNotIn(f, line.lower(), f"Forbidden money term '{f}' found in why_sells for {aud}")


class TestV3BScoutIntegration(unittest.TestCase):
    """Test suite covering full scout run, candidate rejection, formatting and approvals."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()
        scout.connect(self.db_path).close()

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_59_run_scout_generates_5_ideas_with_audience_and_format(self):
        ideas = scout.run_scout(db_path=self.db_path)
        self.assertEqual(len(ideas), 5)
        for it in ideas:
            self.assertIn("audience", it)
            self.assertIn("format", it)
            self.assertIn("why_sells", it)
            self.assertTrue(scout.is_builder_format_supported(it["format"]))

    def test_60_run_scout_includes_why_sells_in_approval_payload_and_review(self):
        ideas = scout.run_scout(db_path=self.db_path)
        conn = guardrails.connect(self.db_path)
        rows = conn.execute("SELECT payload, review FROM approvals WHERE kind = 'scout_idea'").fetchall()
        conn.close()

        self.assertEqual(len(rows), 5)
        for payload, review in rows:
            self.assertIn("Why this sells:", payload)
            self.assertIn("Why sells:", review)

    def test_61_run_scout_filters_out_unsupported_formats(self):
        unsupported = [
            {"category": "Printables", "idea": "Boho Botanical Minimalist Wall Art Poster for Students",
             "demand_score": 25, "competition_score": 25, "fit_score": 25, "effort_score": 25, "reason": "Nice art",
             "format": "wall art", "audience": "students"},
            {"category": "Templates", "idea": "Startup Investor Pitch Deck Presentation Slides for Freelancers",
             "demand_score": 25, "competition_score": 25, "fit_score": 25, "effort_score": 25, "reason": "Slides",
             "format": "slides", "audience": "freelancers"},
            {"category": "Templates", "idea": "Professional Corporate HTML Email Signature for Job Seekers",
             "demand_score": 25, "competition_score": 25, "fit_score": 25, "effort_score": 25, "reason": "Email sig",
             "format": "email signature", "audience": "job seekers"},
            {"category": "Planners", "idea": "Lesson Planning Printable Planner for Teachers",
             "demand_score": 22, "competition_score": 22, "fit_score": 22, "effort_score": 22, "reason": "Good",
             "format": "printable planner", "audience": "teachers"},
            {"category": "Templates", "idea": "Monthly Order Tracker Sheet for Home Bakers",
             "demand_score": 21, "competition_score": 21, "fit_score": 21, "effort_score": 21, "reason": "Good",
             "format": "tracker sheet", "audience": "home bakers"},
        ]
        with mock.patch("scout.get_candidate_ideas", return_value=[scout.score_candidate(c) for c in unsupported]):
            ideas = scout.run_scout(db_path=self.db_path)
            # Slides have no builder, so they are filtered out. Wall art (V4B) and
            # email signature (V4C) now have builders and may pass.
            formats = [i.get("format") for i in ideas]
            self.assertNotIn("slides", formats)

    def test_62_run_scout_filters_out_60d_similar_drafts(self):
        # Insert a draft from 15 days ago into approvals
        now = datetime.datetime.now(datetime.timezone.utc)
        ts = (now - datetime.timedelta(days=15)).isoformat()
        conn = guardrails.connect(self.db_path)
        conn.execute(
            "INSERT INTO approvals (ts, kind, title, payload) VALUES (?, 'public_draft', 'Classroom Lesson Planning Printable Planner for Teachers', 'p')",
            (ts,)
        )
        conn.commit()
        conn.close()

        ideas = scout.run_scout(db_path=self.db_path)
        titles = [i["idea"] for i in ideas]
        for t in titles:
            self.assertNotIn("Classroom Lesson Planning Printable Planner for Teachers", t)

    def test_63_run_scout_respects_max_two_ideas_per_audience_per_week(self):
        # Seed an audience with 2 ideas in the last 7 days
        now = datetime.datetime.now(datetime.timezone.utc)
        ts = (now - datetime.timedelta(days=2)).isoformat()
        conn = scout.connect(self.db_path)
        conn.execute("INSERT INTO scout_ideas (ts, idea, audience) VALUES (?, 'Idea 1', 'teachers')", (ts,))
        conn.execute("INSERT INTO scout_ideas (ts, idea, audience) VALUES (?, 'Idea 2', 'teachers')", (ts,))
        conn.commit()
        conn.close()

        ideas = scout.run_scout(db_path=self.db_path)
        proposed_audiences = [i.get("audience") for i in ideas]
        self.assertNotIn("teachers", proposed_audiences)

    def test_64_score_candidate_preserves_custom_audience_and_format(self):
        raw = {
            "category": "Printables",
            "idea": "Baking Formula Workbook for Home Bakers",
            "demand_score": 20, "competition_score": 20, "fit_score": 20, "effort_score": 20,
            "audience": "home bakers", "format": "workbook"
        }
        scored = scout.score_candidate(raw)
        self.assertEqual(scored["audience"], "home bakers")
        self.assertEqual(scored["format"], "workbook")
        self.assertIn("bakers", scored["why_sells"].lower())


if __name__ == "__main__":
    unittest.main()
