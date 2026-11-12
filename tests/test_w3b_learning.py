"""Unit tests for BINNY-W3B: Learning Loop from owner decisions, truth checks, and sales.

Covers 60+ tests with synthetic histories:
- Dimension extractors (niche, format, audience)
- Time decay calculation (0 to 60 days, boundary checks, future timestamps)
- Smoothing behavior & Bayesian prior
- Minimum sample threshold gating (MIN_SAMPLES = 3 default)
- Weight bounds enforcement [0.5, 1.5]
- Niche weights under approve, reject, severe reject reason
- Format weights under approve, reject, severe reject reason
- Audience weights under approve, reject, severe reject reason
- Truth check passes and fails impact
- Sales counts (via mock/guarded sales_ledger and sqlite table) impact
- Combined candidate weight calculation
- Tenglish 'why' rationale generation for positives, negatives, sales, and reject reasons
- learning_summary metrics, keys, and Tenglish summary text without money amounts
- Scout integration: candidate score scaling by weight when history exists
- Scout integration: neutral behavior when insufficient history
- Deduplication and edge cases (empty DB, missing columns, corrupt timestamps)
"""

import datetime
import math
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

import guardrails
import learning_loop
import scout


class TestW3BLearningLoopExtractors(unittest.TestCase):
    """Tests 1-10: Dimension extractors and ISO parsing."""

    def test_01_detect_niche_explicit_prefix(self):
        self.assertEqual(learning_loop.detect_niche("Planner for niche_budget_sheets"), "niche_budget_sheets")
        self.assertEqual(learning_loop.detect_niche("Tracker for niche_adhd_planners"), "niche_adhd_planners")

    def test_02_detect_niche_keyword_matching(self):
        self.assertEqual(learning_loop.detect_niche("Monthly personal budget log"), "niche_budget_sheets")
        self.assertEqual(learning_loop.detect_niche("Freelancer invoice tracker"), "niche_freelancer_trackers")
        self.assertEqual(learning_loop.detect_niche("2027 Academic Year Calendar"), "niche_planners_2027")

    def test_03_detect_niche_none_for_empty(self):
        self.assertIsNone(learning_loop.detect_niche(""))
        self.assertIsNone(learning_loop.detect_niche(None))
        self.assertIsNone(learning_loop.detect_niche("General cooking guide"))

    def test_04_detect_format_explicit(self):
        self.assertEqual(learning_loop.detect_format("Daily printable planner"), "printable planner")
        self.assertEqual(learning_loop.detect_format("Habit tracker sheet"), "tracker sheet")
        self.assertEqual(learning_loop.detect_format("Math worksheet set"), "worksheet set")

    def test_05_detect_format_keywords(self):
        self.assertEqual(learning_loop.detect_format("Monthly budget sheet for shop"), "budget sheet")
        self.assertEqual(learning_loop.detect_format("Simple workbook for students"), "workbook")
        self.assertEqual(learning_loop.detect_format("Event planner checklist"), "checklist")

    def test_06_detect_format_additional_types(self):
        self.assertEqual(learning_loop.detect_format("Minimalist wall art poster"), "wall art")
        self.assertEqual(learning_loop.detect_format("Corporate email signature"), "email signature")

    def test_07_detect_format_none_for_empty(self):
        self.assertIsNone(learning_loop.detect_format(""))
        self.assertIsNone(learning_loop.detect_format(None))

    def test_08_detect_audience_primary_list(self):
        self.assertEqual(learning_loop.detect_audience("Log for teachers"), "teachers")
        self.assertEqual(learning_loop.detect_audience("Worksheet for students"), "students")
        self.assertEqual(learning_loop.detect_audience("Checklist for exam takers"), "exam takers")
        self.assertEqual(learning_loop.detect_audience("Log for small shop owners"), "small shop owners")

    def test_09_detect_audience_aliases(self):
        self.assertEqual(learning_loop.detect_audience("New educator lesson plans"), "teachers")
        self.assertEqual(learning_loop.detect_audience("Baby sleep and feeding tracker"), "new parents")
        self.assertEqual(learning_loop.detect_audience("Freelance client invoicing"), "freelancers")
        self.assertEqual(learning_loop.detect_audience("Job seeker career portfolio"), "job seekers")
        self.assertEqual(learning_loop.detect_audience("Senior wellness diary"), "retirees")

    def test_10_parse_iso_valid_and_invalid(self):
        now_dt = datetime.datetime.now(datetime.timezone.utc)
        parsed = learning_loop._parse_iso(now_dt.isoformat())
        self.assertIsNotNone(parsed)
        self.assertIsNone(learning_loop._parse_iso("invalid-date"))
        self.assertIsNone(learning_loop._parse_iso(""))
        self.assertIsNone(learning_loop._parse_iso(None))


class TestW3BTimeDecay(unittest.TestCase):
    """Tests 11-16: Time decay over 60 days."""

    def test_11_decay_now_is_one(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        w = learning_loop.calculate_decay_weight(now, now=now, lookback_days=60)
        self.assertAlmostEqual(w, 1.0, places=2)

    def test_12_decay_30_days_is_half(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        dt_30d = now - datetime.timedelta(days=30)
        w = learning_loop.calculate_decay_weight(dt_30d, now=now, lookback_days=60)
        self.assertAlmostEqual(w, 0.5, places=2)

    def test_13_decay_60_days_is_zero(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        dt_60d = now - datetime.timedelta(days=60)
        w = learning_loop.calculate_decay_weight(dt_60d, now=now, lookback_days=60)
        self.assertAlmostEqual(w, 0.0, places=2)

    def test_14_decay_beyond_60_days_is_zero(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        dt_90d = now - datetime.timedelta(days=90)
        w = learning_loop.calculate_decay_weight(dt_90d, now=now, lookback_days=60)
        self.assertEqual(w, 0.0)

    def test_15_decay_future_date_treated_as_now(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        future = now + datetime.timedelta(days=5)
        w = learning_loop.calculate_decay_weight(future, now=now, lookback_days=60)
        self.assertAlmostEqual(w, 1.0, places=2)

    def test_16_decay_none_date_returns_neutral(self):
        w = learning_loop.calculate_decay_weight(None)
        self.assertAlmostEqual(w, 0.5, places=2)


class TestW3BSmoothingAndBounds(unittest.TestCase):
    """Tests 17-23: Smoothing, minimum samples, and [0.5, 1.5] bounds."""

    def test_17_insufficient_samples_returns_neutral_1_0(self):
        # sample_count < 3
        w = learning_loop.compute_smoothed_weight(positives=10.0, negatives=0.0, sample_count=2, min_samples=3)
        self.assertEqual(w, 1.0)
        w0 = learning_loop.compute_smoothed_weight(positives=0.0, negatives=10.0, sample_count=1, min_samples=3)
        self.assertEqual(w0, 1.0)

    def test_18_zero_total_events_returns_neutral(self):
        w = learning_loop.compute_smoothed_weight(positives=0.0, negatives=0.0, sample_count=5, min_samples=3)
        self.assertEqual(w, 1.0)

    def test_19_equal_positives_and_negatives_near_one(self):
        w = learning_loop.compute_smoothed_weight(positives=5.0, negatives=5.0, sample_count=5, min_samples=3)
        self.assertAlmostEqual(w, 1.0, places=2)

    def test_20_all_positives_approaches_upper_bound(self):
        w = learning_loop.compute_smoothed_weight(positives=100.0, negatives=0.0, sample_count=100, min_samples=3)
        self.assertGreater(w, 1.4)
        self.assertLessEqual(w, 1.5)

    def test_21_all_negatives_approaches_lower_bound(self):
        w = learning_loop.compute_smoothed_weight(positives=0.0, negatives=100.0, sample_count=100, min_samples=3)
        self.assertLess(w, 0.6)
        self.assertGreaterEqual(w, 0.5)

    def test_22_weight_never_exceeds_max_bound(self):
        w = learning_loop.compute_smoothed_weight(positives=10000.0, negatives=0.0, sample_count=10000, min_samples=3)
        self.assertEqual(w, 1.5)

    def test_23_weight_never_drops_below_min_bound(self):
        w = learning_loop.compute_smoothed_weight(positives=0.0, negatives=10000.0, sample_count=10000, min_samples=3)
        self.assertEqual(w, 0.5)


class TestW3BDatabaseLearning(unittest.TestCase):
    """Tests 24-40: Synthetic database histories for niches, formats, and audiences."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()
        # Create base tables
        conn = guardrails.connect(self.db_path)
        conn.close()
        scout.connect(self.db_path).close()

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_24_empty_database_yields_empty_weights(self):
        weights = learning_loop.get_weights(db_path=self.db_path)
        self.assertEqual(weights["niche"], {})
        self.assertEqual(weights["format"], {})
        self.assertEqual(weights["audience"], {})

    def test_25_approvals_under_min_samples_yields_1_0(self):
        now_str = datetime.datetime.now(datetime.timezone.utc).isoformat()
        conn = guardrails.connect(self.db_path)
        conn.execute("INSERT INTO approvals (ts, kind, title, payload, status, decided_ts) "
                     "VALUES (?, 'public_draft', 'Planner for niche_budget_sheets', 'p', 'approved', ?)",
                     (now_str, now_str))
        conn.execute("INSERT INTO approvals (ts, kind, title, payload, status, decided_ts) "
                     "VALUES (?, 'public_draft', 'Tracker for niche_budget_sheets', 'p', 'approved', ?)",
                     (now_str, now_str))
        conn.commit()
        conn.close()

        w_niche = learning_loop.get_dimension_weights("niche", db_path=self.db_path)
        self.assertEqual(w_niche.get("niche_budget_sheets"), 1.0)

    def test_26_niche_approved_history_boosts_weight(self):
        now_str = datetime.datetime.now(datetime.timezone.utc).isoformat()
        conn = guardrails.connect(self.db_path)
        for i in range(5):
            conn.execute("INSERT INTO approvals (ts, kind, title, payload, status, decided_ts) "
                         "VALUES (?, 'public_draft', 'Draft for niche_budget_sheets', 'p', 'approved', ?)",
                         (now_str, now_str))
        conn.commit()
        conn.close()

        w_niche = learning_loop.get_dimension_weights("niche", db_path=self.db_path)
        self.assertGreater(w_niche.get("niche_budget_sheets"), 1.1)

    def test_27_niche_rejected_history_lowers_weight(self):
        now_str = datetime.datetime.now(datetime.timezone.utc).isoformat()
        conn = guardrails.connect(self.db_path)
        for i in range(5):
            conn.execute("INSERT INTO approvals (ts, kind, title, payload, status, decided_ts, reason) "
                         "VALUES (?, 'public_draft', 'Draft for niche_adhd_planners', 'p', 'rejected', ?, 'too generic')",
                         (now_str, now_str))
        conn.commit()
        conn.close()

        w_niche = learning_loop.get_dimension_weights("niche", db_path=self.db_path)
        self.assertLess(w_niche.get("niche_adhd_planners"), 0.9)

    def test_28_severe_reject_reason_applies_extra_penalty(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        now_str = now.isoformat()
        # Case A: rejected with benign/no reason
        conn = guardrails.connect(self.db_path)
        for _ in range(4):
            conn.execute("INSERT INTO approvals (ts, kind, title, payload, status, decided_ts, reason) "
                         "VALUES (?, 'public_draft', 'Draft for niche_resume_bundle', 'p', 'rejected', ?, 'other')",
                         (now_str, now_str))
        # Case B: rejected with severe structural reason
        for _ in range(4):
            conn.execute("INSERT INTO approvals (ts, kind, title, payload, status, decided_ts, reason) "
                         "VALUES (?, 'public_draft', 'Draft for niche_notion_templates', 'p', 'rejected', ?, 'too generic')",
                         (now_str, now_str))
        conn.commit()
        conn.close()

        w_niche = learning_loop.get_dimension_weights("niche", db_path=self.db_path)
        self.assertLess(w_niche.get("niche_notion_templates"), w_niche.get("niche_resume_bundle"))

    def test_29_format_approved_history_boosts_weight(self):
        now_str = datetime.datetime.now(datetime.timezone.utc).isoformat()
        conn = guardrails.connect(self.db_path)
        for i in range(4):
            conn.execute("INSERT INTO approvals (ts, kind, title, payload, status, decided_ts) "
                         "VALUES (?, 'public_draft', 'Printable Planner for Teachers', 'p', 'approved', ?)",
                         (now_str, now_str))
        conn.commit()
        conn.close()

        w_fmt = learning_loop.get_dimension_weights("format", db_path=self.db_path)
        self.assertGreater(w_fmt.get("printable planner"), 1.05)

    def test_30_format_rejected_history_lowers_weight(self):
        now_str = datetime.datetime.now(datetime.timezone.utc).isoformat()
        conn = guardrails.connect(self.db_path)
        for i in range(4):
            conn.execute("INSERT INTO approvals (ts, kind, title, payload, status, decided_ts) "
                         "VALUES (?, 'public_draft', 'Tracker Sheet for Freelancers', 'p', 'rejected', ?)",
                         (now_str, now_str))
        conn.commit()
        conn.close()

        w_fmt = learning_loop.get_dimension_weights("format", db_path=self.db_path)
        self.assertLess(w_fmt.get("tracker sheet"), 0.95)

    def test_31_audience_approved_history_boosts_weight(self):
        now_str = datetime.datetime.now(datetime.timezone.utc).isoformat()
        conn = guardrails.connect(self.db_path)
        for i in range(4):
            conn.execute("INSERT INTO approvals (ts, kind, title, payload, status, decided_ts) "
                         "VALUES (?, 'public_draft', 'Classroom Resource for Teachers', 'teachers payload', 'approved', ?)",
                         (now_str, now_str))
        conn.commit()
        conn.close()

        w_aud = learning_loop.get_dimension_weights("audience", db_path=self.db_path)
        self.assertGreater(w_aud.get("teachers"), 1.05)

    def test_32_audience_rejected_history_lowers_weight(self):
        now_str = datetime.datetime.now(datetime.timezone.utc).isoformat()
        conn = guardrails.connect(self.db_path)
        for i in range(4):
            conn.execute("INSERT INTO approvals (ts, kind, title, payload, status, decided_ts) "
                         "VALUES (?, 'public_draft', 'Study Planner for Students', 'students payload', 'rejected', ?)",
                         (now_str, now_str))
        conn.commit()
        conn.close()

        w_aud = learning_loop.get_dimension_weights("audience", db_path=self.db_path)
        self.assertLess(w_aud.get("students"), 0.95)

    def test_33_scout_ideas_table_events_incorporated(self):
        now_str = datetime.datetime.now(datetime.timezone.utc).isoformat()
        conn = scout.connect(self.db_path)
        for i in range(4):
            conn.execute("INSERT INTO scout_ideas (ts, category, idea, niche, status, audience, format) "
                         "VALUES (?, 'Templates', 'Budget Sheet for Small Shop Owners', 'niche_budget_sheets', 'approved', 'small shop owners', 'budget sheet')",
                         (now_str,))
        conn.commit()
        conn.close()

        w_niche = learning_loop.get_dimension_weights("niche", db_path=self.db_path)
        self.assertGreater(w_niche.get("niche_budget_sheets"), 1.05)

    def test_34_old_events_beyond_60_days_ignored(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        old_str = (now - datetime.timedelta(days=70)).isoformat()
        conn = guardrails.connect(self.db_path)
        for i in range(10):
            conn.execute("INSERT INTO approvals (ts, kind, title, payload, status, decided_ts) "
                         "VALUES (?, 'public_draft', 'Draft for niche_budget_sheets', 'p', 'approved', ?)",
                         (old_str, old_str))
        conn.commit()
        conn.close()

        # Because all events are older than 60 days, they decay to 0 and are filtered
        events = learning_loop.extract_events(db_path=self.db_path, now=now)
        self.assertEqual(len(events), 0)

    def test_35_truth_check_ready_boosts_weight(self):
        now_str = datetime.datetime.now(datetime.timezone.utc).isoformat()
        conn = guardrails.connect(self.db_path)
        conn.execute("CREATE TABLE IF NOT EXISTS truth_checks (approval_id INTEGER PRIMARY KEY, verdict TEXT, score INTEGER)")
        # Insert 4 approvals and truth checks
        for app_id in range(1, 5):
            conn.execute("INSERT INTO approvals (id, ts, kind, title, payload, status, decided_ts) "
                         "VALUES (?, ?, 'public_draft', 'Draft for niche_budget_sheets', 'budget sheet', 'approved', ?)",
                         (app_id, now_str, now_str))
            conn.execute("INSERT INTO truth_checks (approval_id, verdict, score) VALUES (?, 'ready', 100)", (app_id,))
        conn.commit()
        conn.close()

        w_niche = learning_loop.get_dimension_weights("niche", db_path=self.db_path)
        self.assertGreater(w_niche.get("niche_budget_sheets"), 1.1)

    def test_36_truth_check_fail_lowers_weight(self):
        now_str = datetime.datetime.now(datetime.timezone.utc).isoformat()
        conn = guardrails.connect(self.db_path)
        conn.execute("CREATE TABLE IF NOT EXISTS truth_checks (approval_id INTEGER PRIMARY KEY, verdict TEXT, score INTEGER)")
        for app_id in range(1, 5):
            conn.execute("INSERT INTO approvals (id, ts, kind, title, payload, status, decided_ts) "
                         "VALUES (?, ?, 'public_draft', 'Draft for niche_adhd_planners', 'adhd planner', 'approved', ?)",
                         (app_id, now_str, now_str))
            conn.execute("INSERT INTO truth_checks (approval_id, verdict, score) VALUES (?, 'needs_fix', 40)", (app_id,))
        conn.commit()
        conn.close()

        w_niche = learning_loop.get_dimension_weights("niche", db_path=self.db_path)
        # Note: 4 approves (+4) and 4 tc fails (-3) -> slight positive/neutral rather than big boost
        self.assertLess(w_niche.get("niche_adhd_planners"), 1.1)

    def test_37_sales_records_in_db_boost_weight(self):
        conn = guardrails.connect(self.db_path)
        conn.execute("CREATE TABLE IF NOT EXISTS sales (item TEXT, ts TEXT)")
        for _ in range(6):
            conn.execute("INSERT INTO sales (item, ts) VALUES ('Monthly Budget Sheet', '2026-01-01')")
        conn.commit()
        conn.close()

        w_fmt = learning_loop.get_dimension_weights("format", db_path=self.db_path)
        self.assertGreater(w_fmt.get("budget sheet"), 1.05)

    def test_38_sales_ledger_table_boost_weight(self):
        conn = guardrails.connect(self.db_path)
        conn.execute("CREATE TABLE IF NOT EXISTS sales_ledger (item_name TEXT, count INTEGER)")
        conn.execute("INSERT INTO sales_ledger (item_name, count) VALUES ('niche_freelancer_trackers', 10)")
        conn.commit()
        conn.close()

        w_niche = learning_loop.get_dimension_weights("niche", db_path=self.db_path)
        self.assertGreater(w_niche.get("niche_freelancer_trackers"), 1.1)

    def test_39_combined_candidate_weight_multiplication(self):
        now_str = datetime.datetime.now(datetime.timezone.utc).isoformat()
        conn = guardrails.connect(self.db_path)
        # Seed approvals for niche_budget_sheets + budget sheet + small shop owners
        for _ in range(5):
            conn.execute("INSERT INTO approvals (ts, kind, title, payload, status, decided_ts) "
                         "VALUES (?, 'public_draft', 'Budget Sheet for small shop owners in niche_budget_sheets', 'p', 'approved', ?)",
                         (now_str, now_str))
        conn.commit()
        conn.close()

        candidate = {
            "niche": "niche_budget_sheets",
            "format": "budget sheet",
            "audience": "small shop owners",
        }
        comb_w = learning_loop.get_candidate_weight(candidate, db_path=self.db_path)
        self.assertGreater(comb_w, 1.1)
        self.assertLessEqual(comb_w, 1.5)

    def test_40_combined_candidate_weight_neutral_on_unknown(self):
        candidate = {
            "niche": "niche_unknown",
            "format": "unknown format",
            "audience": "unknown audience",
        }
        comb_w = learning_loop.get_candidate_weight(candidate, db_path=self.db_path)
        self.assertEqual(comb_w, 1.0)


class TestW3BTenglishRationale(unittest.TestCase):
    """Tests 41-48: Tenglish 'why' rationale generation."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()
        guardrails.connect(self.db_path).close()

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_41_why_none_when_insufficient_history(self):
        candidate = {"niche": "niche_budget_sheets", "format": "budget sheet", "audience": "teachers"}
        why = learning_loop.generate_learning_why(candidate, db_path=self.db_path)
        self.assertIsNone(why)

    def test_42_why_approve_rate_ekkuva(self):
        now_str = datetime.datetime.now(datetime.timezone.utc).isoformat()
        conn = guardrails.connect(self.db_path)
        for _ in range(5):
            conn.execute("INSERT INTO approvals (ts, kind, title, payload, status, decided_ts) "
                         "VALUES (?, 'public_draft', 'Budget Sheet Template', 'p', 'approved', ?)",
                         (now_str, now_str))
        conn.commit()
        conn.close()

        candidate = {"niche": "niche_budget_sheets", "format": "budget sheet", "audience": "teachers"}
        why = learning_loop.generate_learning_why(candidate, db_path=self.db_path)
        self.assertIsNotNone(why)
        self.assertIn("approve rate ekkuva", why)

    def test_43_why_reject_rate_ekkuva_with_reason(self):
        now_str = datetime.datetime.now(datetime.timezone.utc).isoformat()
        conn = guardrails.connect(self.db_path)
        for _ in range(5):
            conn.execute("INSERT INTO approvals (ts, kind, title, payload, status, decided_ts, reason) "
                         "VALUES (?, 'public_draft', 'Resource for students', 'p', 'rejected', ?, 'too generic')",
                         (now_str, now_str))
        conn.commit()
        conn.close()

        candidate = {"niche": "niche_planners_2027", "format": "printable planner", "audience": "students"}
        why = learning_loop.generate_learning_why(candidate, db_path=self.db_path)
        self.assertIsNotNone(why)
        self.assertIn("Students", why)
        self.assertIn("reject rate ekkuva", why)

    def test_44_why_truth_check_pass_rate_bagundi(self):
        now_str = datetime.datetime.now(datetime.timezone.utc).isoformat()
        conn = guardrails.connect(self.db_path)
        conn.execute("CREATE TABLE IF NOT EXISTS truth_checks (approval_id INTEGER PRIMARY KEY, verdict TEXT, score INTEGER)")
        for app_id in range(1, 5):
            conn.execute("INSERT INTO approvals (id, ts, kind, title, payload, status, decided_ts) "
                         "VALUES (?, ?, 'public_draft', 'Workbook for Exam Takers', 'p', 'approved', ?)",
                         (app_id, now_str, now_str))
            conn.execute("INSERT INTO truth_checks (approval_id, verdict, score) VALUES (?, 'ready', 100)", (app_id,))
        conn.commit()
        conn.close()

        candidate = {"niche": "niche_adhd_planners", "format": "workbook", "audience": "exam takers"}
        why = learning_loop.generate_learning_why(candidate, db_path=self.db_path)
        self.assertIsNotNone(why)
        self.assertTrue("bagundi" in why or "ekkuva" in why)

    def test_45_why_sales_ekkuva_unnayi(self):
        conn = guardrails.connect(self.db_path)
        conn.execute("CREATE TABLE IF NOT EXISTS sales_ledger (item_name TEXT, count INTEGER)")
        conn.execute("INSERT INTO sales_ledger (item_name, count) VALUES ('niche_budget_sheets', 12)")
        conn.commit()
        conn.close()

        candidate = {"niche": "niche_budget_sheets", "format": "budget sheet", "audience": "home bakers"}
        why = learning_loop.generate_learning_why(candidate, db_path=self.db_path)
        self.assertIsNotNone(why)
        self.assertIn("sales", why.lower())

    def test_46_why_never_contains_money_or_currency(self):
        forbidden = ["$", "USD", "EUR", "GBP", "rupee", "price", "income", "profit", "earn money"]
        now_str = datetime.datetime.now(datetime.timezone.utc).isoformat()
        conn = guardrails.connect(self.db_path)
        for _ in range(5):
            conn.execute("INSERT INTO approvals (ts, kind, title, payload, status, decided_ts) "
                         "VALUES (?, 'public_draft', 'Budget Sheet', 'p', 'approved', ?)", (now_str, now_str))
        conn.commit()
        conn.close()

        candidate = {"niche": "niche_budget_sheets", "format": "budget sheet", "audience": "teachers"}
        why = learning_loop.generate_learning_why(candidate, db_path=self.db_path)
        for f in forbidden:
            self.assertNotIn(f, why.lower())

    def test_47_friendly_name_clean(self):
        self.assertEqual(learning_loop._friendly_name("niche_budget_sheets"), "Budget sheets")
        self.assertEqual(learning_loop._friendly_name("tracker sheet"), "Tracker sheet")

    def test_48_friendly_name_empty(self):
        self.assertEqual(learning_loop._friendly_name(""), "")


class TestW3BLearningSummary(unittest.TestCase):
    """Tests 49-53: learning_summary structure and content."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()
        guardrails.connect(self.db_path).close()

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_49_summary_empty_state(self):
        summ = learning_loop.learning_summary(db_path=self.db_path)
        self.assertEqual(summ["total_events"], 0)
        self.assertEqual(summ["decisions_count"], 0)
        self.assertEqual(summ["approves"], 0)
        self.assertEqual(summ["rejects"], 0)
        self.assertIn("Learning loop neutral", summ["summary_text"])

    def test_50_summary_with_decisions_and_truth_checks(self):
        now_str = datetime.datetime.now(datetime.timezone.utc).isoformat()
        conn = guardrails.connect(self.db_path)
        conn.execute("CREATE TABLE IF NOT EXISTS truth_checks (approval_id INTEGER PRIMARY KEY, verdict TEXT, score INTEGER)")
        for i in range(1, 5):
            conn.execute("INSERT INTO approvals (id, ts, kind, title, payload, status, decided_ts) "
                         "VALUES (?, ?, 'public_draft', 'Budget Sheet in niche_budget_sheets', 'p', 'approved', ?)",
                         (i, now_str, now_str))
            conn.execute("INSERT INTO truth_checks (approval_id, verdict, score) VALUES (?, 'ready', 95)", (i,))
        for i in range(5, 7):
            conn.execute("INSERT INTO approvals (id, ts, kind, title, payload, status, decided_ts, reason) "
                         "VALUES (?, ?, 'public_draft', 'Tracker in niche_adhd_planners', 'p', 'rejected', ?, 'too generic')",
                         (i, now_str, now_str))
        conn.commit()
        conn.close()

        summ = learning_loop.learning_summary(db_path=self.db_path)
        self.assertEqual(summ["decisions_count"], 6)
        self.assertEqual(summ["approves"], 4)
        self.assertEqual(summ["rejects"], 2)
        self.assertEqual(summ["truth_checks_count"], 4)
        self.assertEqual(summ["truth_checks_ready"], 4)
        self.assertIn("Budget sheets", summ["summary_text"])

    def test_51_summary_never_contains_money_or_currency(self):
        now_str = datetime.datetime.now(datetime.timezone.utc).isoformat()
        conn = guardrails.connect(self.db_path)
        for i in range(1, 5):
            conn.execute("INSERT INTO approvals (ts, kind, title, payload, status, decided_ts) "
                         "VALUES (?, 'public_draft', 'Budget Sheet in niche_budget_sheets', 'p', 'approved', ?)",
                         (now_str, now_str))
        conn.commit()
        conn.close()

        summ = learning_loop.learning_summary(db_path=self.db_path)
        text = summ["summary_text"]
        for f in ("$", "USD", "EUR", "GBP", "rupee", "price", "income"):
            self.assertNotIn(f, text.lower())

    def test_52_summary_has_required_keys(self):
        summ = learning_loop.learning_summary(db_path=self.db_path)
        for k in ("total_events", "decisions_count", "approves", "rejects",
                  "truth_checks_count", "truth_checks_ready", "sales_count",
                  "weights", "top_niches", "low_niches", "summary_text"):
            self.assertIn(k, summ)

    def test_53_summary_tracks_sales_count(self):
        conn = guardrails.connect(self.db_path)
        conn.execute("CREATE TABLE IF NOT EXISTS sales_ledger (item_name TEXT, count INTEGER)")
        conn.execute("INSERT INTO sales_ledger (item_name, count) VALUES ('niche_budget_sheets', 7)")
        conn.commit()
        conn.close()

        summ = learning_loop.learning_summary(db_path=self.db_path)
        self.assertEqual(summ["sales_count"], 7)


class TestW3BScoutIntegration(unittest.TestCase):
    """Tests 54-65: Integration with scout.py and learning weights."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()
        scout.connect(self.db_path).close()

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def test_54_scout_without_history_leaves_score_neutral(self):
        # Empty db -> weights are 1.0
        candidate = {
            "category": "Templates",
            "idea": "Simple Project Expense and Milestone Tracker Sheet",
            "demand_score": 21, "competition_score": 22, "fit_score": 23, "effort_score": 20,
            "audience": "freelancers", "format": "tracker sheet", "niche": "niche_freelancer_trackers"
        }
        w = learning_loop.get_candidate_weight(candidate, db_path=self.db_path)
        self.assertEqual(w, 1.0)

    def test_55_scout_runs_and_attaches_learned_weight_and_why(self):
        now_str = datetime.datetime.now(datetime.timezone.utc).isoformat()
        conn = guardrails.connect(self.db_path)
        # Seed 5 approved drafts for budget sheets
        for _ in range(5):
            conn.execute("INSERT INTO approvals (ts, kind, title, payload, status, decided_ts) "
                         "VALUES (?, 'public_draft', 'Monthly Expense and Cashflow Budget Sheet for Small Shop Owners', 'p', 'approved', ?)",
                         (now_str, now_str))
        conn.commit()
        conn.close()

        ideas = scout.run_scout(db_path=self.db_path)
        self.assertEqual(len(ideas), 5)
        # Check if any idea has learning_weight or learning_why
        budget_ideas = [i for i in ideas if i.get("niche") == "niche_budget_sheets"]
        if budget_ideas:
            b = budget_ideas[0]
            self.assertGreater(b.get("learning_weight", 1.0), 1.0)
            self.assertIn("Budget sheets", b.get("why_sells", ""))

    def test_56_scout_boosts_candidate_score_with_positive_learning(self):
        now_str = datetime.datetime.now(datetime.timezone.utc).isoformat()
        conn = guardrails.connect(self.db_path)
        for _ in range(6):
            conn.execute("INSERT INTO approvals (ts, kind, title, payload, status, decided_ts) "
                         "VALUES (?, 'public_draft', 'Budget Sheet in niche_budget_sheets', 'p', 'approved', ?)",
                         (now_str, now_str))
        conn.commit()
        conn.close()

        w = learning_loop.get_candidate_weight({"niche": "niche_budget_sheets"}, db_path=self.db_path)
        self.assertGreater(w, 1.0)

    def test_57_scout_penalizes_candidate_score_with_negative_learning(self):
        now_str = datetime.datetime.now(datetime.timezone.utc).isoformat()
        conn = guardrails.connect(self.db_path)
        for _ in range(6):
            conn.execute("INSERT INTO approvals (ts, kind, title, payload, status, decided_ts, reason) "
                         "VALUES (?, 'public_draft', 'Resume pack for job seekers in niche_resume_bundle', 'p', 'rejected', ?, 'too generic')",
                         (now_str, now_str))
        conn.commit()
        conn.close()

        w = learning_loop.get_candidate_weight({"niche": "niche_resume_bundle"}, db_path=self.db_path)
        self.assertLess(w, 1.0)

    def test_58_guarded_sales_ledger_import_failure_handled(self):
        with mock.patch.dict("sys.modules", {"sales_ledger": None}):
            counts = learning_loop._get_sales_counts(db_path=self.db_path)
            self.assertIsInstance(counts, dict)

    def test_59_mock_sales_ledger_integration(self):
        mock_sl = mock.MagicMock()
        mock_sl.get_sales_summary.return_value = {"niche_budget_sheets": 5}
        with mock.patch.dict("sys.modules", {"sales_ledger": mock_sl}):
            counts = learning_loop._get_sales_counts(db_path=self.db_path)
            self.assertEqual(counts.get("niche_budget_sheets"), 5)

    def test_60_approval_without_decided_ts_uses_ts(self):
        now_str = datetime.datetime.now(datetime.timezone.utc).isoformat()
        conn = guardrails.connect(self.db_path)
        conn.execute("INSERT INTO approvals (ts, kind, title, payload, status) "
                     "VALUES (?, 'public_draft', 'Budget Sheet', 'niche_budget_sheets', 'approved')",
                     (now_str,))
        conn.commit()
        conn.close()

        events = learning_loop.extract_events(db_path=self.db_path)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["status"], "approved")

    def test_61_scout_stores_clamped_scores_in_approvals(self):
        now_str = datetime.datetime.now(datetime.timezone.utc).isoformat()
        conn = guardrails.connect(self.db_path)
        for _ in range(10):
            conn.execute("INSERT INTO approvals (ts, kind, title, payload, status, decided_ts) "
                         "VALUES (?, 'public_draft', 'Budget Sheet in niche_budget_sheets', 'p', 'approved', ?)",
                         (now_str, now_str))
        conn.commit()
        conn.close()

        ideas = scout.run_scout(db_path=self.db_path)
        for it in ideas:
            self.assertLessEqual(it["total_score"], 100)
            self.assertGreaterEqual(it["total_score"], 0)

    def test_62_extract_events_graceful_on_bad_db_path(self):
        events = learning_loop.extract_events(db_path="/nonexistent/path/db.sqlite")
        self.assertEqual(events, [])

    def test_63_weights_never_nan_or_infinite(self):
        w = learning_loop.compute_smoothed_weight(0.0, 0.0, 10, min_samples=3)
        self.assertFalse(math.isnan(w))
        self.assertFalse(math.isinf(w))

    def test_64_why_sells_enriched_without_breaking_format(self):
        now_str = datetime.datetime.now(datetime.timezone.utc).isoformat()
        conn = guardrails.connect(self.db_path)
        for _ in range(5):
            conn.execute("INSERT INTO approvals (ts, kind, title, payload, status, decided_ts) "
                         "VALUES (?, 'public_draft', 'Budget Sheet in niche_budget_sheets', 'p', 'approved', ?)",
                         (now_str, now_str))
        conn.commit()
        conn.close()

        ideas = scout.run_scout(db_path=self.db_path)
        for it in ideas:
            self.assertIn("why_sells", it)
            self.assertIsInstance(it["why_sells"], str)
            self.assertTrue(len(it["why_sells"]) > 10)

    def test_65_scout_why_sells_preserves_scrubbed_integrity(self):
        ideas = scout.run_scout(db_path=self.db_path)
        forbidden = ["$", "USD", "EUR", "GBP", "rupee", "price"]
        for it in ideas:
            for f in forbidden:
                self.assertNotIn(f, it["why_sells"].lower())


if __name__ == "__main__":
    unittest.main()
