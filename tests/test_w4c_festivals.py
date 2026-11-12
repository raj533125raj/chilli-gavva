"""tests/test_w4c_festivals.py — Unit tests for Telugu festival & seasonal engine.

Tests:
1. Festival calendar registrations and data structure completeness.
2. 2026, 2027, 2028 festival dates (Sankranti, Ugadi, Sri Rama Navami, Vinayaka Chavithi,
   Bathukamma, Dasara, Deepavali, Karthika Masam, Christmas, New Year).
3. Seasonal window dates for exams, back to school, and tax seasons in India and US.
4. IST Timezone normalization (UTC naive, tz-aware, datetime vs date, day boundary switches).
5. Year boundary transitions (e.g. Dec 31 to Jan 1).
6. `is_festival()` behavior for exact dates, multi-day ranges, and off-days.
7. `upcoming()` lookahead filtering across 0, 7, 30, 45, 90, 365 days.
8. `greeting_te()` responses for all festivals, unknown IDs, and dictionary objects.
9. `seasonal_ideas.py` seed generation and format_registry.can_build feasibility.
10. Currency absence and safety compliance across all festival metadata and generated ideas.
"""

from __future__ import annotations

import datetime
import re
import unittest
from zoneinfo import ZoneInfo

import festival_calendar
import format_registry
import seasonal_ideas

CURRENCY_PATTERN = re.compile(
    r"(?:\$|₹|\b(?:USD|INR|Rs\.?)\b)\s*\d|\d[\d,.]*\s*(?:\$|₹|\b(?:USD|INR|Rs\.?)\b)",
    re.IGNORECASE,
)


class TestFestivalCalendarData(unittest.TestCase):
    """Test registry and metadata structures for all festivals."""

    def test_all_festivals_registered(self):
        fests = festival_calendar.all_festivals()
        self.assertGreaterEqual(len(fests), 15)

    def test_expected_festival_keys_present(self):
        expected_ids = [
            "bhogi", "sankranti", "kanuma", "ugadi", "sri_rama_navami",
            "vinayaka_chavithi", "bathukamma", "dasara", "deepavali",
            "karthika_masam", "christmas", "new_year", "exam_season_india",
            "exam_season_us", "back_to_school_india", "back_to_school_us",
            "tax_season_us", "tax_season_india",
        ]
        for fid in expected_ids:
            fest = festival_calendar.get_festival(fid)
            self.assertIsNotNone(fest, f"Festival {fid} should be registered")
            self.assertEqual(fest["id"], fid)
            self.assertIn("name", fest)
            self.assertIn("telugu_name", fest)
            self.assertIn("greeting_te", fest)
            self.assertIn("dates", fest)

    def test_supported_years_coverage(self):
        for fest in festival_calendar.all_festivals():
            for yr in (2026, 2027, 2028):
                self.assertIn(yr, fest["dates"], f"Year {yr} missing in {fest['id']}")
                start, end = fest["dates"][yr]
                self.assertIsInstance(start, datetime.date)
                self.assertIsInstance(end, datetime.date)
                self.assertLessEqual(start, end)

    def test_no_currency_in_festival_metadata(self):
        for fest in festival_calendar.all_festivals():
            for field in ("description", "greeting_en", "greeting_te"):
                text = fest.get(field, "")
                match = CURRENCY_PATTERN.search(text)
                self.assertIsNone(match, f"Currency found in {fest['id']} {field}: {text}")


class TestFestivalDates2026(unittest.TestCase):
    """Test specific 2026 dates according to astronomical and government calendars."""

    def test_sankranti_trinity_2026(self):
        bhogi = festival_calendar.get_festival("bhogi")["dates"][2026]
        sankranti = festival_calendar.get_festival("sankranti")["dates"][2026]
        kanuma = festival_calendar.get_festival("kanuma")["dates"][2026]
        self.assertEqual(bhogi, (datetime.date(2026, 1, 14), datetime.date(2026, 1, 14)))
        self.assertEqual(sankranti, (datetime.date(2026, 1, 15), datetime.date(2026, 1, 15)))
        self.assertEqual(kanuma, (datetime.date(2026, 1, 16), datetime.date(2026, 1, 16)))

    def test_ugadi_2026(self):
        ugadi = festival_calendar.get_festival("ugadi")["dates"][2026]
        self.assertEqual(ugadi, (datetime.date(2026, 3, 19), datetime.date(2026, 3, 19)))

    def test_sri_rama_navami_2026(self):
        srn = festival_calendar.get_festival("sri_rama_navami")["dates"][2026]
        self.assertEqual(srn, (datetime.date(2026, 3, 27), datetime.date(2026, 3, 27)))

    def test_vinayaka_chavithi_2026(self):
        vc = festival_calendar.get_festival("vinayaka_chavithi")["dates"][2026]
        self.assertEqual(vc, (datetime.date(2026, 9, 14), datetime.date(2026, 9, 14)))

    def test_bathukamma_2026(self):
        bk = festival_calendar.get_festival("bathukamma")["dates"][2026]
        self.assertEqual(bk, (datetime.date(2026, 10, 11), datetime.date(2026, 10, 19)))

    def test_dasara_2026(self):
        dasara = festival_calendar.get_festival("dasara")["dates"][2026]
        self.assertEqual(dasara, (datetime.date(2026, 10, 20), datetime.date(2026, 10, 20)))

    def test_deepavali_2026(self):
        diwali = festival_calendar.get_festival("deepavali")["dates"][2026]
        self.assertEqual(diwali, (datetime.date(2026, 11, 8), datetime.date(2026, 11, 8)))

    def test_karthika_masam_2026(self):
        km = festival_calendar.get_festival("karthika_masam")["dates"][2026]
        self.assertEqual(km, (datetime.date(2026, 11, 10), datetime.date(2026, 12, 9)))

    def test_fixed_holidays_2026(self):
        ny = festival_calendar.get_festival("new_year")["dates"][2026]
        xmas = festival_calendar.get_festival("christmas")["dates"][2026]
        self.assertEqual(ny, (datetime.date(2026, 1, 1), datetime.date(2026, 1, 1)))
        self.assertEqual(xmas, (datetime.date(2026, 12, 25), datetime.date(2026, 12, 25)))


class TestFestivalDates2027(unittest.TestCase):
    """Test specific 2027 festival dates."""

    def test_ugadi_2027(self):
        ugadi = festival_calendar.get_festival("ugadi")["dates"][2027]
        self.assertEqual(ugadi, (datetime.date(2027, 4, 7), datetime.date(2027, 4, 7)))

    def test_sri_rama_navami_2027(self):
        srn = festival_calendar.get_festival("sri_rama_navami")["dates"][2027]
        self.assertEqual(srn, (datetime.date(2027, 4, 15), datetime.date(2027, 4, 15)))

    def test_vinayaka_chavithi_2027(self):
        vc = festival_calendar.get_festival("vinayaka_chavithi")["dates"][2027]
        self.assertEqual(vc, (datetime.date(2027, 9, 4), datetime.date(2027, 9, 4)))

    def test_bathukamma_2027(self):
        bk = festival_calendar.get_festival("bathukamma")["dates"][2027]
        self.assertEqual(bk, (datetime.date(2027, 9, 30), datetime.date(2027, 10, 8)))

    def test_dasara_2027(self):
        dasara = festival_calendar.get_festival("dasara")["dates"][2027]
        self.assertEqual(dasara, (datetime.date(2027, 10, 10), datetime.date(2027, 10, 10)))

    def test_deepavali_2027(self):
        diwali = festival_calendar.get_festival("deepavali")["dates"][2027]
        self.assertEqual(diwali, (datetime.date(2027, 10, 29), datetime.date(2027, 10, 29)))

    def test_karthika_masam_2027(self):
        km = festival_calendar.get_festival("karthika_masam")["dates"][2027]
        self.assertEqual(km, (datetime.date(2027, 10, 30), datetime.date(2027, 11, 28)))


class TestFestivalDates2028(unittest.TestCase):
    """Test specific 2028 festival dates (leap year)."""

    def test_ugadi_2028(self):
        ugadi = festival_calendar.get_festival("ugadi")["dates"][2028]
        self.assertEqual(ugadi, (datetime.date(2028, 3, 27), datetime.date(2028, 3, 27)))

    def test_sri_rama_navami_2028(self):
        srn = festival_calendar.get_festival("sri_rama_navami")["dates"][2028]
        self.assertEqual(srn, (datetime.date(2028, 4, 4), datetime.date(2028, 4, 4)))

    def test_vinayaka_chavithi_2028(self):
        vc = festival_calendar.get_festival("vinayaka_chavithi")["dates"][2028]
        self.assertEqual(vc, (datetime.date(2028, 8, 24), datetime.date(2028, 8, 24)))

    def test_bathukamma_2028(self):
        bk = festival_calendar.get_festival("bathukamma")["dates"][2028]
        self.assertEqual(bk, (datetime.date(2028, 9, 19), datetime.date(2028, 9, 27)))

    def test_dasara_2028(self):
        dasara = festival_calendar.get_festival("dasara")["dates"][2028]
        self.assertEqual(dasara, (datetime.date(2028, 9, 29), datetime.date(2028, 9, 29)))

    def test_deepavali_2028(self):
        diwali = festival_calendar.get_festival("deepavali")["dates"][2028]
        self.assertEqual(diwali, (datetime.date(2028, 10, 17), datetime.date(2028, 10, 17)))

    def test_karthika_masam_2028(self):
        km = festival_calendar.get_festival("karthika_masam")["dates"][2028]
        self.assertEqual(km, (datetime.date(2028, 10, 18), datetime.date(2028, 11, 16)))


class TestSeasonalWindows(unittest.TestCase):
    """Test academic and financial seasonal windows."""

    def test_exam_seasons(self):
        india = festival_calendar.get_festival("exam_season_india")
        us = festival_calendar.get_festival("exam_season_us")
        for yr in (2026, 2027, 2028):
            self.assertEqual(india["dates"][yr], (datetime.date(yr, 2, 15), datetime.date(yr, 4, 15)))
            self.assertEqual(us["dates"][yr], (datetime.date(yr, 4, 20), datetime.date(yr, 5, 25)))

    def test_back_to_school_seasons(self):
        bts_in = festival_calendar.get_festival("back_to_school_india")
        bts_us = festival_calendar.get_festival("back_to_school_us")
        for yr in (2026, 2027, 2028):
            self.assertEqual(bts_in["dates"][yr], (datetime.date(yr, 6, 1), datetime.date(yr, 7, 15)))
            self.assertEqual(bts_us["dates"][yr], (datetime.date(yr, 8, 1), datetime.date(yr, 9, 15)))

    def test_tax_seasons(self):
        tax_us = festival_calendar.get_festival("tax_season_us")
        tax_in = festival_calendar.get_festival("tax_season_india")
        for yr in (2026, 2027, 2028):
            self.assertEqual(tax_us["dates"][yr], (datetime.date(yr, 1, 15), datetime.date(yr, 4, 15)))
            self.assertEqual(tax_in["dates"][yr], (datetime.date(yr, 6, 1), datetime.date(yr, 7, 31)))


class TestTimezoneAndISTHandling(unittest.TestCase):
    """Test date parsing and IST conversion edge cases."""

    def test_to_ist_date_with_date_object(self):
        d = datetime.date(2026, 1, 15)
        self.assertEqual(festival_calendar._to_ist_date(d), d)

    def test_to_ist_date_with_none(self):
        res = festival_calendar._to_ist_date(None)
        now_ist = datetime.datetime.now(festival_calendar.IST).date()
        self.assertEqual(res, now_ist)

    def test_to_ist_date_naive_datetime_assumes_utc(self):
        # 2026-01-14 20:00:00 UTC is 2026-01-15 01:30:00 IST (Sankranti day!)
        dt_naive = datetime.datetime(2026, 1, 14, 20, 0, 0)
        res = festival_calendar._to_ist_date(dt_naive)
        self.assertEqual(res, datetime.date(2026, 1, 15))

    def test_to_ist_date_aware_datetime(self):
        # 2026-01-15 02:00:00 JST (+09:00) is 2026-01-14 22:30:00 IST (Bhogi day!)
        jst = ZoneInfo("Asia/Tokyo")
        dt_aware = datetime.datetime(2026, 1, 15, 2, 0, 0, tzinfo=jst)
        res = festival_calendar._to_ist_date(dt_aware)
        self.assertEqual(res, datetime.date(2026, 1, 14))

    def test_to_ist_date_invalid_type(self):
        with self.assertRaises(TypeError):
            festival_calendar._to_ist_date("2026-01-15")


class TestIsFestivalFunction(unittest.TestCase):
    """Test is_festival() single date matching and categories."""

    def test_is_festival_on_bhogi(self):
        matches = festival_calendar.is_festival(datetime.date(2026, 1, 14))
        ids = [m["id"] for m in matches]
        self.assertIn("bhogi", ids)

    def test_is_festival_on_sankranti(self):
        matches = festival_calendar.is_festival(datetime.date(2026, 1, 15))
        ids = [m["id"] for m in matches]
        self.assertIn("sankranti", ids)
        self.assertIn("tax_season_us", ids)

    def test_is_festival_on_kanuma(self):
        matches = festival_calendar.is_festival(datetime.date(2026, 1, 16))
        ids = [m["id"] for m in matches]
        self.assertIn("kanuma", ids)

    def test_is_festival_on_ugadi_2026(self):
        matches = festival_calendar.is_festival(datetime.date(2026, 3, 19))
        ids = [m["id"] for m in matches]
        self.assertIn("ugadi", ids)

    def test_is_festival_on_bathukamma_midday(self):
        # 2026 Bathukamma is Oct 11 to Oct 19
        matches = festival_calendar.is_festival(datetime.date(2026, 10, 15))
        ids = [m["id"] for m in matches]
        self.assertIn("bathukamma", ids)

    def test_is_festival_on_karthika_masam(self):
        # 2026 Karthika Masam is Nov 10 to Dec 9
        matches = festival_calendar.is_festival(datetime.date(2026, 11, 20))
        ids = [m["id"] for m in matches]
        self.assertIn("karthika_masam", ids)

    def test_is_festival_with_category_filter(self):
        matches_fest = festival_calendar.is_festival(datetime.date(2026, 1, 15), category="festival")
        ids_fest = [m["id"] for m in matches_fest]
        self.assertIn("sankranti", ids_fest)
        self.assertNotIn("tax_season_us", ids_fest)

        matches_season = festival_calendar.is_festival(datetime.date(2026, 1, 15), category="season")
        ids_season = [m["id"] for m in matches_season]
        self.assertNotIn("sankranti", ids_season)
        self.assertIn("tax_season_us", ids_season)

    def test_is_festival_on_regular_day_no_event(self):
        # A day with no festival (e.g. May 28, 2026)
        matches = festival_calendar.is_festival(datetime.date(2026, 5, 28))
        self.assertEqual(len(matches), 0)


class TestUpcomingFunction(unittest.TestCase):
    """Test upcoming() chronological lookahead."""

    def test_upcoming_from_early_jan_2026(self):
        results = festival_calendar.upcoming(datetime.date(2026, 1, 2), days=15)
        ids = [r["id"] for r in results]
        self.assertIn("bhogi", ids)
        self.assertIn("sankranti", ids)
        self.assertIn("kanuma", ids)
        self.assertNotIn("ugadi", ids)

    def test_upcoming_ordering(self):
        results = festival_calendar.upcoming(datetime.date(2026, 1, 10), days=30)
        start_dates = [r["start_date"] for r in results]
        self.assertEqual(start_dates, sorted(start_dates))

    def test_upcoming_zero_days(self):
        # Zero days lookahead should return only events active today
        results = festival_calendar.upcoming(datetime.date(2026, 1, 15), days=0)
        ids = [r["id"] for r in results]
        self.assertIn("sankranti", ids)

    def test_upcoming_category_filter(self):
        results = festival_calendar.upcoming(datetime.date(2026, 1, 10), days=30, category="festival")
        for r in results:
            self.assertEqual(r["category"], "festival")

    def test_upcoming_days_until_calculation(self):
        base = datetime.date(2026, 1, 10)
        results = festival_calendar.upcoming(base, days=10)
        bhogi = next(r for r in results if r["id"] == "bhogi")
        self.assertEqual(bhogi["days_until_start"], 4)
        self.assertEqual(bhogi["is_in_progress"], False)

    def test_upcoming_in_progress_event(self):
        base = datetime.date(2026, 10, 15)  # During Bathukamma
        results = festival_calendar.upcoming(base, days=10)
        bk = next(r for r in results if r["id"] == "bathukamma")
        self.assertTrue(bk["is_in_progress"])
        self.assertLessEqual(bk["days_until_start"], 0)


class TestYearBoundaryTransitions(unittest.TestCase):
    """Test behavior around year-end boundaries."""

    def test_year_end_boundary_dec_31(self):
        dec_31 = datetime.date(2026, 12, 31)
        up = festival_calendar.upcoming(dec_31, days=5)
        ids = [r["id"] for r in up]
        self.assertIn("new_year", ids)
        ny = next(r for r in up if r["id"] == "new_year")
        self.assertEqual(ny["active_year"], 2027)
        self.assertEqual(ny["days_until_start"], 1)

    def test_year_start_boundary_jan_1(self):
        jan_1 = datetime.date(2027, 1, 1)
        matches = festival_calendar.is_festival(jan_1)
        ids = [m["id"] for m in matches]
        self.assertIn("new_year", ids)

    def test_leap_year_feb_29_2028(self):
        # 2028 is a leap year; Feb 29, 2028 falls in Exam Season India
        matches = festival_calendar.is_festival(datetime.date(2028, 2, 29))
        ids = [m["id"] for m in matches]
        self.assertIn("exam_season_india", ids)


class TestGreetingTeFunction(unittest.TestCase):
    """Test Tenglish greetings for all festivals and edge cases."""

    def test_greeting_te_for_all_festivals(self):
        for fest in festival_calendar.all_festivals():
            greeting = festival_calendar.greeting_te(fest["id"])
            self.assertIsInstance(greeting, str)
            self.assertTrue(len(greeting) > 10)
            # Festivals have subhakankshalu, seasons have supportive Tenglish greetings
            if fest.get("category") in ("festival", "holiday"):
                self.assertIn("subhakankshalu", greeting.lower())
            self.assertIsNone(CURRENCY_PATTERN.search(greeting))

    def test_greeting_te_by_dict(self):
        fest_dict = festival_calendar.get_festival("ugadi")
        greeting = festival_calendar.greeting_te(fest_dict)
        self.assertEqual(greeting, fest_dict["greeting_te"])

    def test_greeting_te_unknown_festival(self):
        greeting = festival_calendar.greeting_te("unknown_festival_123")
        self.assertIn("subhakankshalu", greeting)

    def test_greeting_te_none_or_empty(self):
        greeting = festival_calendar.greeting_te("")
        self.assertIn("subhakankshalu", greeting)


class TestSeasonalIdeas(unittest.TestCase):
    """Test seasonal idea seed generation, filtering, and format validation."""

    def test_all_blueprints_are_buildable(self):
        for fid, blueprints in seasonal_ideas.IDEA_BLUEPRINTS.items():
            for bp in blueprints:
                can = format_registry.can_build(bp)
                self.assertTrue(can, f"Blueprint {bp['title']} ({bp['format']}) cannot be built by format_registry")

    def test_get_seasonal_ideas_returns_buildable_candidates(self):
        # Look ahead 45 days from Jan 1, 2026
        ideas = seasonal_ideas.get_seasonal_ideas(datetime.date(2026, 1, 1), days=45)
        self.assertGreater(len(ideas), 0)
        for idea in ideas:
            self.assertTrue(idea["buildable"])
            self.assertTrue(format_registry.can_build(idea))
            self.assertIn("title", idea)
            self.assertIn("niche", idea)
            self.assertIn("format", idea)
            self.assertIn("audience", idea)
            self.assertIn("why", idea)
            self.assertIn("festival_id", idea)
            self.assertIn("festival_name", idea)
            self.assertIsNone(CURRENCY_PATTERN.search(idea["title"]))
            self.assertIsNone(CURRENCY_PATTERN.search(idea["why"]))

    def test_get_seasonal_ideas_limit(self):
        ideas = seasonal_ideas.get_seasonal_ideas(datetime.date(2026, 1, 1), days=45, limit=2)
        self.assertEqual(len(ideas), 2)

    def test_get_seasonal_ideas_no_duplicates(self):
        ideas = seasonal_ideas.get_seasonal_ideas(datetime.date(2026, 1, 1), days=90)
        titles = [i["title"] for i in ideas]
        self.assertEqual(len(titles), len(set(titles)))

    def test_summary_te_output(self):
        summary = seasonal_ideas.summary_te(datetime.date(2026, 1, 1), days=45)
        self.assertIsInstance(summary, str)
        self.assertIn("buildable seasonal product seeds", summary)
        self.assertIsNone(CURRENCY_PATTERN.search(summary))

    def test_summary_te_quiet_period(self):
        # In late May 2026 (outside active festivals/seasons for 7 days)
        summary = seasonal_ideas.summary_te(datetime.date(2026, 5, 27), days=2)
        self.assertIn("evergreen products focus cheyochu", summary)


class TestSeasonalIdeasScenariosAcrossYears(unittest.TestCase):
    """Test idea generation across multiple seasonal windows in 2026, 2027, 2028."""

    def test_ugadi_ideas_2026(self):
        # 2 weeks before Ugadi 2026 (March 5, 2026)
        ideas = seasonal_ideas.get_seasonal_ideas(datetime.date(2026, 3, 5), days=30)
        fids = [i["festival_id"] for i in ideas]
        self.assertIn("ugadi", fids)

    def test_vinayaka_chavithi_ideas_2027(self):
        # 10 days before Vinayaka Chavithi 2027 (Aug 25, 2027)
        ideas = seasonal_ideas.get_seasonal_ideas(datetime.date(2027, 8, 25), days=20)
        fids = [i["festival_id"] for i in ideas]
        self.assertIn("vinayaka_chavithi", fids)

    def test_bathukamma_and_dasara_ideas_2028(self):
        # Sep 10, 2028 -> Bathukamma starts Sep 19, Dasara Sep 29
        ideas = seasonal_ideas.get_seasonal_ideas(datetime.date(2028, 9, 10), days=25)
        fids = [i["festival_id"] for i in ideas]
        self.assertIn("bathukamma", fids)
        self.assertIn("dasara", fids)

    def test_deepavali_ideas_2026(self):
        # Oct 20, 2026 -> Deepavali on Nov 8
        ideas = seasonal_ideas.get_seasonal_ideas(datetime.date(2026, 10, 20), days=25)
        fids = [i["festival_id"] for i in ideas]
        self.assertIn("deepavali", fids)

    def test_back_to_school_ideas_india(self):
        # May 20, 2027 -> India back to school starts June 1
        ideas = seasonal_ideas.get_seasonal_ideas(datetime.date(2027, 5, 20), days=30)
        fids = [i["festival_id"] for i in ideas]
        self.assertIn("back_to_school_india", fids)

    def test_tax_season_ideas_us(self):
        # Jan 10, 2028 -> US tax season starts Jan 15
        ideas = seasonal_ideas.get_seasonal_ideas(datetime.date(2028, 1, 10), days=20)
        fids = [i["festival_id"] for i in ideas]
        self.assertIn("tax_season_us", fids)


if __name__ == "__main__":
    unittest.main()
