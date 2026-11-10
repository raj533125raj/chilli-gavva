"""Unit tests for checker.py: BINNY-V3A rebuilt rubric, sub-scores, hard caps, and score spread.

Covers:
  - 30+ realistic idea texts (good 70-90, generic < 60, unbuildable < 45)
  - Sub-scores breakdown verification (buyer clarity, audience specificity, buildable format, differentiation, honesty, seasonality)
  - Hard cap: generic title -> max 60
  - Hard cap: same audience phrase as 3+ recent drafts -> -25 penalty
  - Hard cap: format builder cannot make -> max 40
  - Hard cap: vague 'template pack' with no contents -> max 55
  - LLM review constraint: score can only move +/- 10 from rule score
  - Parse failure fallback to rule score
  - Score spread test (not all ideas get identical scores)
  - Contract preservation (check_draft, format_review, parse_review, review_passes)
"""

import os
import re
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import checker
import guardrails

# =====================================================================
# 30+ Realistic Idea / Draft Texts
# =====================================================================

# 12 Good drafts (should score 70 - 95)
GOOD_IDEAS = [
    # 1. ADHD Daily Routine Planner
    """PRODUCT NAME: ADHD Daily Visual Routine Planner
BUYER: Neurodivergent adults who need to structure daily focus blocks and track daily habits.
WHAT'S INSIDE:
- Morning focus routine checklist
- Visual time-blocking daily grid
- Executive dysfunction task breakdown worksheet
- Weekly reflection and energy tracker
LISTING TITLE: ADHD Daily Visual Routine Planner Printable
LISTING DESCRIPTION: Unlike a simple blank spreadsheet or standard calendar, this printable PDF system is designed specifically for neurodivergent minds with low-friction checklists and visual progress cues.
SEARCH TAGS: adhd planner, printable planner, focus tracker, routine planner, neurodivergent""",

    # 2. Freelance Client Rate & Invoice Tracker
    """PRODUCT NAME: Freelance Client Rate & Invoice Tracker
BUYER: Freelancers and independent consultants who need to manage client retainers and irregular income.
WHAT'S INSIDE:
- Client CRM database with payment status
- Automated invoice formula generator
- Quarterly tax calculation sheet
- Project rate calculator
LISTING TITLE: Freelance Client Rate & Invoice Tracker Spreadsheet
LISTING DESCRIPTION: Unlike basic free templates with static cells, this Google Sheets workspace gives freelancers pre-filled formulas and automated totals to streamline billing.
SEARCH TAGS: freelancer tracker, invoice tracker, client sheet, google sheets budget, freelancer tools""",

    # 3. High School Teacher Lesson Planner
    """PRODUCT NAME: High School Teacher Weekly Lesson Planner
BUYER: High school teachers and educators who need to organize semester curriculum and daily class periods.
WHAT'S INSIDE:
- Semester unit planning dashboard
- Daily 5-period printable lesson plan templates
- Student grading and attendance tracker
- Substitute teacher briefing sheet
LISTING TITLE: High School Teacher Weekly Lesson Planner 2027
LISTING DESCRIPTION: Designed specifically for educators, this printable PDF planner beats generic notebooks with structured sections for learning objectives, standards, and homework logs.
SEARCH TAGS: teacher planner, lesson planner, printable planner, classroom organization, 2027 planner""",

    # 4. Job Seeker ATS Resume & Career Bundle
    """PRODUCT NAME: ATS Modern Resume & Job Search Tracker
BUYER: Career switchers and job seekers who want a clean ATS-friendly resume and application tracker.
WHAT'S INSIDE:
- ATS-tested Word resume template
- Matching cover letter template
- Application status spreadsheet tracker
- Interview prep question framework
LISTING TITLE: ATS Modern Resume & Job Search Tracker Bundle
LISTING DESCRIPTION: Unlike generic resume builders that break applicant tracking systems, this clean ATS-compliant template and tracking sheet optimizes your job hunt.
SEARCH TAGS: ats resume, resume template, job tracker, cover letter template, career bundle""",

    # 5. Zero-Based Family Monthly Budget
    """PRODUCT NAME: Zero-Based Family Monthly Budget Dashboard
BUYER: Families and couples who want to plan monthly living expenses and build an emergency fund.
WHAT'S INSIDE:
- Zero-based income allocation sheet
- Recurring bill payment tracker with calendar view
- Debt snowball calculator
- Sinking funds progress dashboard
LISTING TITLE: Zero-Based Family Monthly Budget Dashboard for Sheets
LISTING DESCRIPTION: Unlike simple blank budget sheets, this complete workbook uses automated category formulas and visual charts to guide your family savings step-by-step.
SEARCH TAGS: budget spreadsheet, monthly budget, family budget, google sheets budget, money tracker""",

    # 6. Student Exam Study Timetable
    """PRODUCT NAME: University Exam Revision & Study Timetable
BUYER: College students and exam candidates who need to organize semester revision schedules.
WHAT'S INSIDE:
- Spaced repetition study schedule
- Pomodoro revision session tracker
- Course syllabus checklist
- Exam countdown dashboard
LISTING TITLE: University Exam Revision & Study Timetable Printable
LISTING DESCRIPTION: Built specifically for students facing heavy exam seasons, this printable PDF system beats generic planners with scientifically proven spaced repetition intervals.
SEARCH TAGS: student planner, exam study, revision planner, printable timetable, study tracker""",

    # 7. Solopreneur Content Creation System
    """PRODUCT NAME: Solopreneur Content Calendar & Pipeline
BUYER: Solopreneurs and digital creators who need to plan, write, and schedule social media content.
WHAT'S INSIDE:
- Multi-platform post scheduling calendar
- Content idea repository and hook bank
- Sponsorship and affiliate tracker
- Analytics and engagement log
LISTING TITLE: Solopreneur Content Calendar & Pipeline for Notion
LISTING DESCRIPTION: Compared with chaotic note apps, this organized Notion workspace provides an end-to-end publishing workflow that saves creators hours every week.
SEARCH TAGS: notion template, content calendar, notion planner, creator dashboard, social media tracker""",

    # 8. Wedding Budget & Vendor Organizer
    """PRODUCT NAME: Comprehensive Wedding Budget & Vendor Organizer
BUYER: Engaged couples and wedding planners who need to manage ceremony budgets and vendor contracts.
WHAT'S INSIDE:
- Itemized category budget sheet with payment deadlines
- Vendor comparison and contact directory
- Guest RSVP and seating arrangement tracker
- Day-of timeline master schedule
LISTING TITLE: Wedding Budget & Vendor Organizer Spreadsheet
LISTING DESCRIPTION: Unlike basic wedding checklists, this Google Sheets template automatically calculates budget variances, tax tips, and remaining balances in real time.
SEARCH TAGS: wedding planner, wedding budget, google sheets template, event organizer, bride checklist""",

    # 9. Small Etsy Shop Inventory & Profit Calculator
    """PRODUCT NAME: Etsy Shop Inventory & Profit Margin Calculator
BUYER: Etsy sellers and handmade craft business owners who need to track raw material costs and listing fees.
WHAT'S INSIDE:
- Batch pricing and profit margin calculator
- Raw materials inventory reorder sheet
- Order fulfillment tracker
- Monthly revenue dashboard
LISTING TITLE: Etsy Shop Inventory & Profit Margin Calculator for Sheets
LISTING DESCRIPTION: Built specifically for handmade sellers, this automated spreadsheet calculates exact marketplace fees, shipping costs, and net margins.
SEARCH TAGS: etsy seller, profit calculator, small business sheet, inventory tracker, pricing spreadsheet""",

    # 10. Nurse Shift Rotation & Wellness Journal
    """PRODUCT NAME: Nurse Shift Rotation & Wellness Planner
BUYER: Hospital nurses and shift workers who need to organize rotating 12-hour shifts and recovery sleep.
WHAT'S INSIDE:
- Day/Night rotating shift calendar grid
- Hydration and meal prep log for long shifts
- Fatigue and recovery sleep tracker
- Continuing education hours log
LISTING TITLE: Nurse Shift Rotation & Wellness Planner Printable PDF
LISTING DESCRIPTION: Unlike standard Monday-to-Friday planners, this printable system accommodates irregular night shifts and protects healthcare worker wellbeing.
SEARCH TAGS: nurse planner, shift worker, printable planner, wellness journal, healthcare schedule""",

    # 11. Minimalist 2027 Annual Dated Planner
    """PRODUCT NAME: Minimalist 2027 Annual Dated Planner
BUYER: Professionals and planners who need a distraction-free daily and weekly agenda for the upcoming year.
WHAT'S INSIDE:
- Full year 2027 dated overview
- 12 monthly planning spreads with priority blocks
- 52 weekly structured layouts
- Annual goal breakdown sheets
LISTING TITLE: Minimalist 2027 Annual Dated Planner Printable
LISTING DESCRIPTION: Unlike cluttered commercial agendas, this hyper-clean PDF layout provides ample writing space and intuitive weekly hierarchy.
SEARCH TAGS: 2027 planner, dated planner, printable planner, annual calendar, minimalist agenda""",

    # 12. Remote Worker Ergonomic Habit & Time Tracker
    """PRODUCT NAME: Remote Worker Time-Block & Habit System
BUYER: Remote workers and distributed employees who need to maintain daily routines and work-life boundaries.
WHAT'S INSIDE:
- Time-blocking daily focus sheets
- Break reminder and posture checklist
- Meeting note capture dashboard
- End-of-day shutdown routine template
LISTING TITLE: Remote Worker Time-Block & Habit System for Notion
LISTING DESCRIPTION: Specifically crafted for work-from-home professionals, this Notion template establishes clear boundaries and prevents remote work burnout.
SEARCH TAGS: notion template, remote worker, productivity dashboard, habit tracker, time blocking"""
]

# 10 Generic drafts (should score < 60)
GENERIC_IDEAS = [
    # 1. Bare generic title
    """PRODUCT NAME: Template Pack
BUYER: People who want things organized.
WHAT'S INSIDE:
- Some files
LISTING TITLE: Template Pack
LISTING DESCRIPTION: Good templates for everyone.
SEARCH TAGS: template, pack""",

    # 2. Vague digital product
    """PRODUCT NAME: Digital Product
BUYER: General users who need digital tools.
WHAT'S INSIDE:
- Document
LISTING TITLE: Digital Product
LISTING DESCRIPTION: A digital product that helps you do tasks.
SEARCH TAGS: product, digital""",

    # 3. Simple planner with no specificity
    """PRODUCT NAME: Planner
BUYER: Anyone who wants to plan.
WHAT'S INSIDE:
- Pages
LISTING TITLE: Planner
LISTING DESCRIPTION: Nice planner for daily use.
SEARCH TAGS: planner, daily""",

    # 4. Ultimate Starter Pack
    """PRODUCT NAME: Starter Pack
BUYER: Buyers looking for a starter pack.
WHAT'S INSIDE:
- Starter items
LISTING TITLE: Starter Pack
LISTING DESCRIPTION: Everything you need in one starter pack.
SEARCH TAGS: starter, pack""",

    # 5. Generic tracker
    """PRODUCT NAME: Tracker
BUYER: Folks who want to track.
WHAT'S INSIDE:
- Tracker sheet
LISTING TITLE: Tracker
LISTING DESCRIPTION: Track things easily with this file.
SEARCH TAGS: tracker, file""",

    # 6. Productivity template
    """PRODUCT NAME: Productivity Template
BUYER: People interested in productivity.
WHAT'S INSIDE:
- Helpful sheets
LISTING TITLE: Productivity Template
LISTING DESCRIPTION: Be more productive every day.
SEARCH TAGS: productivity, template""",

    # 7. Digital Planner generic
    """PRODUCT NAME: Digital Planner
BUYER: People who like planning.
WHAT'S INSIDE:
- Planner file
LISTING TITLE: Digital Planner
LISTING DESCRIPTION: Plan your schedule.
SEARCH TAGS: planner, digital""",

    # 8. All-in-one pack
    """PRODUCT NAME: All-In-One Pack
BUYER: Customers who need an all-in-one pack.
WHAT'S INSIDE:
- All items
LISTING TITLE: All-In-One Pack
LISTING DESCRIPTION: An all in one pack for your needs.
SEARCH TAGS: bundle, pack""",

    # 9. Simple template
    """PRODUCT NAME: Simple Template
BUYER: Someone who needs a simple file.
WHAT'S INSIDE:
- Notes
LISTING TITLE: Simple Template
LISTING DESCRIPTION: A simple template for general use.
SEARCH TAGS: simple, template""",

    # 10. Bundle Pack
    """PRODUCT NAME: Bundle Pack
BUYER: Anyone looking for a bundle.
WHAT'S INSIDE:
- Bundle documents
LISTING TITLE: Bundle Pack
LISTING DESCRIPTION: Multiple templates together.
SEARCH TAGS: bundle, pack"""
]

# 10 Unbuildable drafts (apps, SaaS, physical, courses, hardware) (should score < 45)
UNBUILDABLE_IDEAS = [
    # 1. iOS / Android Mobile App
    """PRODUCT NAME: Smart Diet Tracking Mobile App
BUYER: Fitness enthusiasts who want real-time barcode scanning.
WHAT'S INSIDE:
- Swift iOS native code
- Android APK release
- Cloud database sync
LISTING TITLE: Smart Diet Tracking Mobile App for iOS and Android
LISTING DESCRIPTION: Download the complete mobile app on App Store to track calories in real time.
SEARCH TAGS: mobile app, ios app, android app, diet tracker""",

    # 2. SaaS Web Application
    """PRODUCT NAME: AI CRM SaaS Platform
BUYER: Sales teams needing cloud-hosted pipeline automation.
WHAT'S INSIDE:
- Multi-tenant backend service
- PostgreSQL hosted database
- Web application interface
LISTING TITLE: AI CRM SaaS Platform Subscription
LISTING DESCRIPTION: A modern web application providing cloud software for sales prospecting.
SEARCH TAGS: saas, web app, cloud software, crm""",

    # 3. Video Masterclass Course
    """PRODUCT NAME: 10-Hour Masterclass Video Course
BUYER: Beginners wanting filmed tutorial modules.
WHAT'S INSIDE:
- 10 hours of 4K video course footage
- Audio course MP3 downloads
- Masterclass slides
LISTING TITLE: 10-Hour Masterclass Video Course
LISTING DESCRIPTION: Watch full filmed lessons and stream video modules on demanding topics.
SEARCH TAGS: video course, masterclass, online course, tutorial""",

    # 4. Physical Hardcover Notebook
    """PRODUCT NAME: Luxury Leather Hardcover Journal
BUYER: Journalers who love premium stationery.
WHAT'S INSIDE:
- 200-page physical book with sewn binding
- Gold-embossed hardcover
- Premium cotton paper
LISTING TITLE: Luxury Leather Hardcover Journal
LISTING DESCRIPTION: A physical book delivered to your doorstep with luxury hardcover binding.
SEARCH TAGS: hardcover, physical book, luxury journal, stationery""",

    # 5. Printed T-shirt & Merchandise
    """PRODUCT NAME: Developer Slogan Graphic T-Shirt
BUYER: Programmers who wear casual tech apparel.
WHAT'S INSIDE:
- 100% combed cotton t-shirt
- Screen-printed graphic merchandise
LISTING TITLE: Developer Slogan Graphic T-Shirt Merchandise
LISTING DESCRIPTION: High quality apparel and physical merchandise shipped directly.
SEARCH TAGS: t-shirt, merchandise, developer apparel, clothing""",

    # 6. Hardware IoT Sensor Device
    """PRODUCT NAME: Smart Home Temperature Sensor Hardware
BUYER: Home automation builders needing physical sensors.
WHAT'S INSIDE:
- ESP32 hardware device
- Soldered temperature sensor board
LISTING TITLE: Smart Home Temperature Sensor Hardware
LISTING DESCRIPTION: Physical hardware unit ready to plug into your wall outlet.
SEARCH TAGS: hardware, iot device, smart home, sensor""",

    # 7. 1-on-1 Consulting Calls
    """PRODUCT NAME: 60-Minute Executive Coaching Call
BUYER: Executives seeking live consulting sessions.
WHAT'S INSIDE:
- 1-on-1 coaching video call
- Live consulting call schedule
LISTING TITLE: 60-Minute 1-on-1 Coaching Call
LISTING DESCRIPTION: Book live hours on Zoom for personal strategy consultation.
SEARCH TAGS: 1-on-1 coaching, consulting call, live advisory, strategy session""",

    # 8. Chrome Extension Software
    """PRODUCT NAME: Social Media Auto-Poster Chrome Extension
BUYER: Marketers needing browser extension automation.
WHAT'S INSIDE:
- Chrome extension manifest v3 package
- Background automation script
LISTING TITLE: Social Media Auto-Poster Chrome Extension
LISTING DESCRIPTION: Install this chrome extension software directly into Google Chrome.
SEARCH TAGS: chrome extension, browser extension, software, automation""",

    # 9. Private Membership Site
    """PRODUCT NAME: Exclusive Mastermind Membership Site
BUYER: Entrepreneurs wanting private community forum access.
WHAT'S INSIDE:
- Membership site login credentials
- Private discussion forum
LISTING TITLE: Exclusive Mastermind Membership Site
LISTING DESCRIPTION: Join an active membership site with monthly recurring access.
SEARCH TAGS: membership site, community access, mastermind, forum""",

    # 10. API Service & Backend Hosting
    """PRODUCT NAME: High-Speed Geocoding API Service
BUYER: Developers building mapping solutions.
WHAT'S INSIDE:
- REST API service endpoints
- Backend service deployment
- Database hosting
LISTING TITLE: High-Speed Geocoding API Service
LISTING DESCRIPTION: Call our backend service with 99.9% uptime for cloud geolocation data.
SEARCH TAGS: api service, backend service, database hosting, web service"""
]


class TestV3ACheckerRubric(unittest.TestCase):
    """Test suite for BINNY-V3A rebuilt checker rubric."""

    def setUp(self):
        # Clear GITHUB_* environment in setUp as required
        patcher = mock.patch.dict(os.environ, {})
        self.addCleanup(patcher.stop)
        patcher.start()
        for k in list(os.environ.keys()):
            if k.startswith("GITHUB_") or k == "RUNNER_ENVIRONMENT":
                del os.environ[k]

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = os.path.join(self.tmp.name, "test_v3a.db")

    # -----------------------------------------------------------------
    # Realistic Idea Tests: Good Ideas (70 - 95)
    # -----------------------------------------------------------------
    def test_01_good_ideas_score_in_target_range(self):
        for i, text in enumerate(GOOD_IDEAS):
            with self.subTest(idea_index=i):
                res = checker.rule_based_fallback(text, topic=f"Good Idea {i}")
                self.assertGreaterEqual(
                    res["score"], 70,
                    f"Good idea {i} scored {res['score']} < 70. Reasons: {res['reasons']}"
                )
                self.assertLessEqual(
                    res["score"], 100,
                    f"Good idea {i} scored {res['score']} > 100."
                )
                self.assertEqual(res["verdict"], "pass")
                self.assertTrue(res["passed"])

    # -----------------------------------------------------------------
    # Realistic Idea Tests: Generic Ideas (< 60)
    # -----------------------------------------------------------------
    def test_02_generic_ideas_score_under_60(self):
        for i, text in enumerate(GENERIC_IDEAS):
            with self.subTest(generic_index=i):
                res = checker.rule_based_fallback(text, topic=f"Generic Idea {i}")
                self.assertLess(
                    res["score"], 60,
                    f"Generic idea {i} scored {res['score']} >= 60. Subscores: {res.get('subscores')}"
                )
                self.assertFalse(res["passed"])
                self.assertIn(res["verdict"], ("revise", "reject"))

    # -----------------------------------------------------------------
    # Realistic Idea Tests: Unbuildable Ideas (< 45)
    # -----------------------------------------------------------------
    def test_03_unbuildable_ideas_score_under_45(self):
        for i, text in enumerate(UNBUILDABLE_IDEAS):
            with self.subTest(unbuildable_index=i):
                res = checker.rule_based_fallback(text, topic=f"Unbuildable Idea {i}")
                self.assertLess(
                    res["score"], 45,
                    f"Unbuildable idea {i} scored {res['score']} >= 45. Caps: {res.get('caps')}"
                )
                self.assertFalse(res["passed"])
                self.assertEqual(res["verdict"], "reject")

    # -----------------------------------------------------------------
    # Score Spread Test: Ensure scores are varied, not all identical
    # -----------------------------------------------------------------
    def test_04_score_spread_across_all_ideas(self):
        all_ideas = GOOD_IDEAS + GENERIC_IDEAS + UNBUILDABLE_IDEAS
        scores = [checker.rule_based_fallback(t, topic=f"Topic {i}")["score"] for i, t in enumerate(all_ideas)]
        unique_scores = set(scores)
        self.assertGreaterEqual(len(unique_scores), 5, f"Expected varied score distribution, got: {unique_scores}")
        self.assertLess(min(scores), 45, "Expected low minimum score for unbuildable/bad ideas")
        self.assertGreaterEqual(max(scores), 75, "Expected high maximum score for good ideas")

    # -----------------------------------------------------------------
    # Sub-scores Breakdown & Weights
    # -----------------------------------------------------------------
    def test_05_subscores_present_and_sum_to_raw_sum(self):
        eval_res = checker.evaluate_subscores(GOOD_IDEAS[0], topic="ADHD Planner")
        sub = eval_res["subscores"]
        required_keys = {
            "buyer_clarity", "specificity_audience", "buildable_format",
            "differentiation", "listing_honesty", "seasonality_fit"
        }
        self.assertTrue(required_keys.issubset(sub.keys()))
        self.assertEqual(eval_res["raw_sum"], sum(sub.values()))

    def test_06_subscores_weight_limits(self):
        eval_res = checker.evaluate_subscores(GOOD_IDEAS[0], topic="ADHD Planner")
        sub = eval_res["subscores"]
        self.assertLessEqual(sub["buyer_clarity"], checker.WEIGHT_BUYER_CLARITY)
        self.assertLessEqual(sub["specificity_audience"], checker.WEIGHT_SPECIFICITY_AUDIENCE)
        self.assertLessEqual(sub["buildable_format"], checker.WEIGHT_BUILDABLE_FORMAT)
        self.assertLessEqual(sub["differentiation"], checker.WEIGHT_DIFFERENTIATION)
        self.assertLessEqual(sub["listing_honesty"], checker.WEIGHT_LISTING_HONESTY)
        self.assertLessEqual(sub["seasonality_fit"], checker.WEIGHT_SEASONALITY_FIT)

    # -----------------------------------------------------------------
    # Hard Cap 1: Generic Title -> max 60
    # -----------------------------------------------------------------
    def test_07_hard_cap_generic_title_caps_at_60(self):
        # A draft with otherwise good features, but a purely generic title
        text = GOOD_IDEAS[0].replace(
            "PRODUCT NAME: ADHD Daily Visual Routine Planner",
            "PRODUCT NAME: Template Pack"
        ).replace(
            "LISTING TITLE: ADHD Daily Visual Routine Planner Printable",
            "LISTING TITLE: Template Pack"
        )
        res = checker.rule_based_fallback(text, topic="Template Pack")
        self.assertLessEqual(res["score"], 60)
        self.assertTrue(any("Generic title" in r for r in res["reasons"]))

    def test_08_hard_cap_bare_word_title_caps_at_60(self):
        res = checker.rule_based_fallback(GOOD_IDEAS[1], topic="Planner")
        # Topic is a single generic word "Planner"
        # Since product text has specific title, test when draft title is also planner
        draft = re.sub(r"PRODUCT NAME:.*", "PRODUCT NAME: Planner", GOOD_IDEAS[1])
        res2 = checker.rule_based_fallback(draft, topic="Planner")
        self.assertLessEqual(res2["score"], 60)

    # -----------------------------------------------------------------
    # Hard Cap 2: Format Builder Cannot Make -> max 40
    # -----------------------------------------------------------------
    def test_09_hard_cap_mobile_app_caps_at_40(self):
        draft = """PRODUCT NAME: Focus Habits Mobile App
BUYER: Freelancers and students needing daily habit tracking.
WHAT'S INSIDE:
- iOS App Swift code
- Android APK download
LISTING TITLE: Focus Habits Mobile App
LISTING DESCRIPTION: An interactive mobile app with cloud database syncing.
SEARCH TAGS: mobile app, habits"""
        res = checker.rule_based_fallback(draft, topic="Mobile App")
        self.assertLessEqual(res["score"], 40)
        self.assertTrue(any("cannot make" in r for r in res["reasons"]))

    def test_10_hard_cap_saas_software_caps_at_40(self):
        draft = """PRODUCT NAME: Invoice SaaS Platform
BUYER: Freelancers needing cloud-hosted billing.
WHAT'S INSIDE:
- SaaS web app subscription
- Cloud database hosting
LISTING TITLE: Invoice SaaS Platform
LISTING DESCRIPTION: Unlike free templates, this SaaS software automates emails.
SEARCH TAGS: saas, web app"""
        res = checker.rule_based_fallback(draft, topic="SaaS Platform")
        self.assertLessEqual(res["score"], 40)

    def test_11_hard_cap_video_course_caps_at_40(self):
        draft = """PRODUCT NAME: Complete Python Video Course
BUYER: Beginners learning to code.
WHAT'S INSIDE:
- 10 hours video course
- Downloadable masterclass modules
LISTING TITLE: Complete Python Video Course
LISTING DESCRIPTION: Video tutorials and lectures.
SEARCH TAGS: video course, tutorials"""
        res = checker.rule_based_fallback(draft, topic="Video Course")
        self.assertLessEqual(res["score"], 40)

    def test_12_hard_cap_physical_book_caps_at_40(self):
        draft = """PRODUCT NAME: Hardcover Notebook
BUYER: Journalers who love luxury paper.
WHAT'S INSIDE:
- Physical book with hardcover binding
LISTING TITLE: Hardcover Notebook
LISTING DESCRIPTION: Shipped physical journal.
SEARCH TAGS: physical book, hardcover"""
        res = checker.rule_based_fallback(draft, topic="Physical Notebook")
        self.assertLessEqual(res["score"], 40)

    # -----------------------------------------------------------------
    # Hard Cap 3: Vague 'template pack' with no contents -> max 55
    # -----------------------------------------------------------------
    def test_13_hard_cap_vague_template_pack_no_contents_caps_at_55(self):
        draft = """PRODUCT NAME: Freelance Template Pack
BUYER: Freelancers who want templates.
LISTING TITLE: Freelance Template Pack
LISTING DESCRIPTION: A great template pack for freelancers to get work done quickly."""
        res = checker.rule_based_fallback(draft, topic="Freelance Template Pack")
        self.assertLessEqual(res["score"], 55)
        self.assertTrue(any("template pack" in r.lower() or "missing" in r.lower() for r in res["reasons"]))

    # -----------------------------------------------------------------
    # Hard Cap 4: Repeated Audience Phrase across 3+ Recent Drafts (-25 penalty)
    # -----------------------------------------------------------------
    def test_14_audience_phrase_repetition_penalty_applied(self):
        recent_drafts = [
            {"id": 1, "title": "Draft 1", "payload": "BUYER: for Remote Creators who want to organize posts."},
            {"id": 2, "title": "Draft 2", "payload": "Target audience: for Remote Creators managing clients."},
            {"id": 3, "title": "Draft 3", "payload": "Designed for Remote Creators building online businesses."},
        ]
        # Draft repeating "for remote creators"
        draft = GOOD_IDEAS[0].replace(
            "BUYER: Neurodivergent adults who need to structure daily focus blocks and track daily habits.",
            "BUYER: for Remote Creators who need daily structure."
        )
        res_with_history = checker.rule_based_fallback(
            draft, topic="Creator Planner", recent_drafts=recent_drafts
        )
        res_without_history = checker.rule_based_fallback(
            draft, topic="Creator Planner", recent_drafts=[]
        )
        self.assertEqual(res_with_history["score"], res_without_history["score"] - 25)
        self.assertTrue(any("Same audience phrase as 3+ recent drafts" in r for r in res_with_history["reasons"]))

    def test_15_audience_phrase_repetition_not_applied_under_threshold(self):
        recent_drafts = [
            {"id": 1, "title": "Draft 1", "payload": "BUYER: for Remote Creators who want to organize posts."},
            {"id": 2, "title": "Draft 2", "payload": "BUYER: for Teachers organizing classrooms."},
        ]
        draft = GOOD_IDEAS[0].replace(
            "BUYER: Neurodivergent adults who need to structure daily focus blocks and track daily habits.",
            "BUYER: for Remote Creators who need daily structure."
        )
        res = checker.rule_based_fallback(draft, topic="Creator Planner", recent_drafts=recent_drafts)
        # Should not suffer -25 repetition penalty because count is only 1 in recent drafts
        self.assertFalse(any("Same audience phrase" in r for r in res["reasons"]))

    def test_16_audience_phrase_repetition_via_db(self):
        conn = guardrails.connect(self.db)
        for i in range(3):
            conn.execute(
                "INSERT INTO approvals (ts, kind, title, payload) VALUES (?, 'public_draft', ?, ?)",
                (f"2026-11-0{i+1}T10:00:00", f"Draft {i+1}", "BUYER: for Remote Creators doing video editing")
            )
        conn.commit()
        conn.close()

        draft = GOOD_IDEAS[0].replace(
            "BUYER: Neurodivergent adults who need to structure daily focus blocks and track daily habits.",
            "BUYER: for Remote Creators who need to plan schedules."
        )
        res = checker.check_draft(draft, topic="Creator Planner", db_path=self.db)
        self.assertTrue(any("Same audience phrase" in r for r in res["reasons"]))

    # -----------------------------------------------------------------
    # LLM Review Constraints: Only move +/- 10 from Rule Score
    # -----------------------------------------------------------------
    def test_17_llm_score_clamped_upper_bound(self):
        # If rule score is 80, but LLM says 100 -> score must be clamped to 90
        rule_eval = checker.rule_based_fallback(GOOD_IDEAS[0], topic="Planner")
        rule_score = rule_eval["score"]

        ctx = {"llm": lambda prompt, size=None: '{"score": 100, "verdict": "pass", "reasons": ["AI says perfect"]}'}
        res = checker.check_draft(GOOD_IDEAS[0], topic="Planner", ctx=ctx)
        self.assertLessEqual(res["score"], min(100, rule_score + 10))
        self.assertEqual(res["score"], min(100, rule_score + 10))

    def test_18_llm_score_clamped_lower_bound(self):
        # If rule score is 85, but LLM says 30 -> score must be clamped to 75
        rule_eval = checker.rule_based_fallback(GOOD_IDEAS[1], topic="Tracker")
        rule_score = rule_eval["score"]

        ctx = {"llm": lambda prompt, size=None: '{"score": 30, "verdict": "reject", "reasons": ["AI harsh"]}'}
        res = checker.check_draft(GOOD_IDEAS[1], topic="Tracker", ctx=ctx)
        self.assertGreaterEqual(res["score"], max(0, rule_score - 10))
        self.assertEqual(res["score"], max(0, rule_score - 10))

    def test_19_llm_score_within_range_preserved(self):
        # If rule score is 85, and LLM says 88 -> score remains 88
        rule_eval = checker.rule_based_fallback(GOOD_IDEAS[1], topic="Tracker")
        rule_score = rule_eval["score"]
        target_score = rule_score + 3

        ctx = {"llm": lambda prompt, size=None: f'{{"score": {target_score}, "verdict": "pass", "reasons": ["AI agreed"]}}'}
        res = checker.check_draft(GOOD_IDEAS[1], topic="Tracker", ctx=ctx)
        self.assertEqual(res["score"], target_score)

    def test_20_llm_cannot_bypass_hard_cap(self):
        # An unbuildable mobile app with rule cap 40. LLM returns 95. Bounded score must remain capped <= 40!
        ctx = {"llm": lambda prompt, size=None: '{"score": 95, "verdict": "pass", "reasons": ["Looks shiny"]}'}
        res = checker.check_draft(UNBUILDABLE_IDEAS[0], topic="Mobile App", ctx=ctx)
        self.assertLessEqual(res["score"], 40)

    # -----------------------------------------------------------------
    # LLM Parse Failures & Fallbacks
    # -----------------------------------------------------------------
    def test_21_llm_empty_response_falls_back_to_rules(self):
        ctx = {"llm": lambda prompt, size=None: ""}
        res = checker.check_draft(GOOD_IDEAS[0], topic="Planner", ctx=ctx)
        self.assertTrue(res["fallback"])
        self.assertGreaterEqual(res["score"], 70)

    def test_22_llm_malformed_json_falls_back_to_rules(self):
        ctx = {"llm": lambda prompt, size=None: "Sorry, I cannot answer as JSON right now."}
        res = checker.check_draft(GOOD_IDEAS[0], topic="Planner", ctx=ctx)
        self.assertTrue(res["fallback"])

    def test_23_llm_exception_falls_back_to_rules(self):
        def crashing_llm(prompt, size=None):
            raise RuntimeError("API timeout")
        ctx = {"llm": crashing_llm}
        res = checker.check_draft(GOOD_IDEAS[0], topic="Planner", ctx=ctx)
        self.assertTrue(res["fallback"])

    # -----------------------------------------------------------------
    # Prohibited Claims: Financial & Leaks
    # -----------------------------------------------------------------
    def test_24_money_claim_deduction(self):
        draft_with_money = GOOD_IDEAS[0] + "\nGuaranteed passive income! Make money fast with zero risk."
        res = checker.rule_based_fallback(draft_with_money, topic="ADHD Planner")
        self.assertLess(res["score"], 70)
        self.assertTrue(any("financial or earnings" in r for r in res["reasons"]))
        self.assertEqual(res["verdict"], "reject")

    def test_25_unapproved_leak_deduction(self):
        draft_with_leak = GOOD_IDEAS[0] + "\nConfidential internal only: private key secret strategy do not share."
        res = checker.rule_based_fallback(draft_with_leak, topic="ADHD Planner")
        self.assertLess(res["score"], 70)
        self.assertTrue(any("unapproved internal" in r for r in res["reasons"]))
        self.assertEqual(res["verdict"], "reject")

    # -----------------------------------------------------------------
    # Empty Draft Handling
    # -----------------------------------------------------------------
    def test_26_empty_draft_returns_zero(self):
        res = checker.check_draft("", topic="Empty")
        self.assertEqual(res["score"], 0)
        self.assertEqual(res["verdict"], "reject")
        self.assertFalse(res["passed"])

    def test_27_none_draft_returns_zero(self):
        res = checker.check_draft(None, topic="None")
        self.assertEqual(res["score"], 0)
        self.assertEqual(res["verdict"], "reject")

    # -----------------------------------------------------------------
    # Format and Parse Review Contracts
    # -----------------------------------------------------------------
    def test_28_format_review_ai_and_parse(self):
        fmt = checker.format_review(86, ["Strong format", "Clear audience"], verdict="pass", fallback=False)
        self.assertIn("score 86/100", fmt)
        self.assertIn("Checker: 86/100", fmt)
        self.assertIn("CHECKER: PASS (AI 86)", fmt)
        score, reasons = checker.parse_review(fmt)
        self.assertEqual(score, 86)
        self.assertEqual(reasons, ["Strong format", "Clear audience"])
        self.assertTrue(checker.review_passes(fmt))

    def test_29_format_review_fallback_and_parse(self):
        fmt = checker.format_review(72, ["Clear structure"], verdict="pass", fallback=True)
        self.assertIn("score 72/100", fmt)
        self.assertIn("Checker: 72/100", fmt)
        self.assertIn("CHECKER: unavailable (checklist only)", fmt)
        score, reasons = checker.parse_review(fmt)
        self.assertEqual(score, 72)
        # Unavailable review should not pass auto approval
        self.assertFalse(checker.review_passes(fmt))

    def test_30_review_passes_boundary_checks(self):
        self.assertFalse(checker.review_passes(None))
        self.assertFalse(checker.review_passes(""))
        self.assertFalse(checker.review_passes("Checker: 69/100 | CHECKER: REVISE"))
        self.assertFalse(checker.review_passes("Checker: 30/100 | CHECKER: REJECT"))
        self.assertTrue(checker.review_passes("Checker: 70/100 | CHECKER: PASS"))
        self.assertTrue(checker.review_passes("Checker: 95/100 | CHECKER: APPROVE"))

    # -----------------------------------------------------------------
    # Additional Granular Sub-score Tests (reaching 60+ tests)
    # -----------------------------------------------------------------
    def test_31_buyer_clarity_high_when_header_and_usecase_present(self):
        eval_res = checker.evaluate_subscores("BUYER: Teachers who need to manage daily grades.", topic="Grading")
        self.assertEqual(eval_res["subscores"]["buyer_clarity"], 20)

    def test_32_buyer_clarity_medium_when_only_header(self):
        eval_res = checker.evaluate_subscores("BUYER: High school teachers.", topic="Grading")
        self.assertEqual(eval_res["subscores"]["buyer_clarity"], 15)

    def test_33_buyer_clarity_low_when_generic(self):
        eval_res = checker.evaluate_subscores("For anyone interested in tasks.", topic="Tasks")
        self.assertEqual(eval_res["subscores"]["buyer_clarity"], 6)

    def test_34_audience_specificity_high_multiple_targets(self):
        eval_res = checker.evaluate_subscores("For freelancers, consultants, and creators.", topic="Business")
        self.assertEqual(eval_res["subscores"]["specificity_audience"], 15)

    def test_35_audience_specificity_medium_single_target(self):
        eval_res = checker.evaluate_subscores("For freelancers.", topic="Business")
        self.assertEqual(eval_res["subscores"]["specificity_audience"], 12)

    def test_36_audience_specificity_low_vague_terms(self):
        eval_res = checker.evaluate_subscores("For general users and people.", topic="General")
        self.assertEqual(eval_res["subscores"]["specificity_audience"], 6)

    def test_37_buildable_format_sheets_high(self):
        eval_res = checker.evaluate_subscores("Google sheets budget spreadsheet tracker.", topic="Sheets")
        self.assertEqual(eval_res["subscores"]["buildable_format"], 25)

    def test_38_buildable_format_notion_high(self):
        eval_res = checker.evaluate_subscores("Notion template workspace dashboard.", topic="Notion")
        self.assertEqual(eval_res["subscores"]["buildable_format"], 25)

    def test_39_buildable_format_planner_pdf_high(self):
        eval_res = checker.evaluate_subscores("Printable planner PDF dated planner calendar.", topic="Planner")
        self.assertEqual(eval_res["subscores"]["buildable_format"], 25)

    def test_40_buildable_format_resume_bundle_high(self):
        eval_res = checker.evaluate_subscores("ATS resume template cover letter job tracker.", topic="Resume")
        self.assertEqual(eval_res["subscores"]["buildable_format"], 25)

    def test_41_buildable_format_unbuildable_low(self):
        eval_res = checker.evaluate_subscores("Download our iOS app and Android app software.", topic="App")
        self.assertEqual(eval_res["subscores"]["buildable_format"], 5)

    def test_42_differentiation_high_when_beats_free_and_features(self):
        text = "Unlike a basic free template, here is:\n- Item 1\n- Item 2\n- Item 3"
        eval_res = checker.evaluate_subscores(text, topic="Tool")
        self.assertEqual(eval_res["subscores"]["differentiation"], 15)

    def test_43_differentiation_medium_when_only_beats_free(self):
        text = "Unlike a basic free template, this tool saves hours."
        eval_res = checker.evaluate_subscores(text, topic="Tool")
        self.assertEqual(eval_res["subscores"]["differentiation"], 10)

    def test_44_differentiation_low_when_no_comparison(self):
        text = "Just a template to do things."
        eval_res = checker.evaluate_subscores(text, topic="Tool")
        self.assertEqual(eval_res["subscores"]["differentiation"], 6)

    def test_45_listing_honesty_full_when_clean(self):
        text = "WHAT'S INSIDE:\n- Clean sheet\n- User guide"
        eval_res = checker.evaluate_subscores(text, topic="Tool")
        self.assertEqual(eval_res["subscores"]["listing_honesty"], 15)

    def test_46_listing_honesty_deduction_when_no_structure(self):
        text = "Short text with no structure"
        eval_res = checker.evaluate_subscores(text, topic="Tool")
        self.assertLess(eval_res["subscores"]["listing_honesty"], 15)

    def test_47_seasonality_fit_high_when_season_and_evergreen(self):
        text = "2027 annual dated planner budget tracker for back to school season."
        eval_res = checker.evaluate_subscores(text, topic="Planner")
        self.assertEqual(eval_res["subscores"]["seasonality_fit"], 10)

    def test_48_seasonality_fit_medium_when_only_season(self):
        text = "Diwali celebration gift guide and checklist."
        eval_res = checker.evaluate_subscores(text, topic="Guide")
        self.assertEqual(eval_res["subscores"]["seasonality_fit"], 8)

    def test_49_seasonality_fit_medium_when_only_evergreen(self):
        text = "Monthly budget planner for freelancers."
        eval_res = checker.evaluate_subscores(text, topic="Budget")
        self.assertEqual(eval_res["subscores"]["seasonality_fit"], 7)

    def test_50_seasonality_fit_baseline_when_neutral(self):
        text = "General digital checklist."
        eval_res = checker.evaluate_subscores(text, topic="Checklist")
        self.assertEqual(eval_res["subscores"]["seasonality_fit"], 4)

    def test_51_extract_audience_phrases_standard(self):
        phrases = checker._extract_audience_phrases("BUYER: for Remote Creators who need to track time.")
        self.assertTrue(any("remote creators" in p for p in phrases))

    def test_52_extract_audience_phrases_empty(self):
        self.assertEqual(checker._extract_audience_phrases(""), [])

    def test_53_check_audience_phrase_repetition_empty_list(self):
        self.assertEqual(checker.check_audience_phrase_repetition([], []), (False, None))

    def test_54_parse_checker_response_none_on_non_dict(self):
        self.assertIsNone(checker.parse_checker_response("[1, 2, 3]"))

    def test_55_parse_checker_response_none_on_boolean_score(self):
        self.assertIsNone(checker.parse_checker_response('{"score": true, "verdict": "pass"}'))

    def test_56_parse_checker_response_infers_verdict_when_missing(self):
        res = checker.parse_checker_response('{"score": 85}')
        self.assertIsNotNone(res)
        self.assertEqual(res["verdict"], "pass")

        res_low = checker.parse_checker_response('{"score": 35}')
        self.assertIsNotNone(res_low)
        self.assertEqual(res_low["verdict"], "reject")

        res_mid = checker.parse_checker_response('{"score": 55}')
        self.assertIsNotNone(res_mid)
        self.assertEqual(res_mid["verdict"], "revise")

    def test_57_parse_checker_response_cleans_long_reasons(self):
        long_reason = "X" * 150
        res = checker.parse_checker_response(f'{{"score": 80, "verdict": "pass", "reasons": ["{long_reason}"]}}')
        self.assertIsNotNone(res)
        self.assertLessEqual(len(res["reasons"][0]), 100)

    def test_58_raw_review_score_edge_cases(self):
        self.assertIsNone(checker._raw_review_score(None))
        self.assertIsNone(checker._raw_review_score(123))
        self.assertIsNone(checker._raw_review_score("no score here"))
        self.assertEqual(checker._raw_review_score("Checker: 77/100"), 77)
        self.assertEqual(checker._raw_review_score("Score 65/100"), 65)

    def test_59_check_draft_merges_reasons_uniquely(self):
        ctx = {"llm": lambda prompt, size=None: '{"score": 85, "verdict": "pass", "reasons": ["AI unique reason", "Explains value over free template"]}'}
        res = checker.check_draft(GOOD_IDEAS[0], topic="Planner", ctx=ctx)
        reasons = res["reasons"]
        self.assertIn("AI unique reason", reasons)
        self.assertIn("Explains value over free template", reasons)
        # Check no duplicates
        self.assertEqual(len(reasons), len(set(reasons)))

    def test_60_unbuildable_hardware_rejected(self):
        res = checker.rule_based_fallback("Physical temperature hardware device with soldered components.", topic="Hardware")
        self.assertEqual(res["verdict"], "reject")
        self.assertLessEqual(res["score"], 40)

    def test_61_unbuildable_coaching_rejected(self):
        res = checker.rule_based_fallback("Book 1-on-1 coaching live consulting call on Zoom.", topic="Coaching")
        self.assertEqual(res["verdict"], "reject")
        self.assertLessEqual(res["score"], 40)

    def test_62_unbuildable_membership_rejected(self):
        res = checker.rule_based_fallback("Monthly subscription to private membership site forum.", topic="Community")
        self.assertEqual(res["verdict"], "reject")
        self.assertLessEqual(res["score"], 40)


if __name__ == "__main__":
    unittest.main()
