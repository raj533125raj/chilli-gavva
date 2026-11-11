"""festival_calendar.py — Telugu Festival and Seasonal Engine for 2026-2028.

Provides accurate date tracking, upcoming event search, festival checks,
and Tenglish greetings for:
- Telugu Festivals:
    * Bhogi, Makara Sankranti, Kanuma (Sankranti 3-day harvest festival)
    * Ugadi (Telugu New Year)
    * Sri Rama Navami (Chaitra Navami)
    * Vinayaka Chavithi (Ganesh Chaturthi)
    * Bathukamma (Telangana Floral Festival) & Dasara (Vijaya Dasami)
    * Deepavali (Diwali & Naraka Chaturdashi)
    * Karthika Masam (Sacred Lunar Month)
- Global Celebrations:
    * Christmas
    * New Year
- Key Seasons:
    * Exam Seasons (India Board/College Exams, US Spring/Finals)
    * Back to School (India Academic Reopening, US Fall Reopening)
    * Tax Seasons (US IRS Filing Season, India ITR Filing Season)

Sources:
- Drik Panchang (Hindu Lunar Calendar & Tithi calculations for 2026, 2027, 2028).
- Andhra Pradesh & Telangana Government Official Gazetted Holiday Lists.
- Central Board of Secondary Education (CBSE) / Andhra/Telangana State Board Exam schedules.
- United States Department of Education / Standard Fall School calendars.
- Internal Revenue Service (IRS) annual filing season dates.
- Income Tax Department, Government of India (Individual ITR filing windows).

Guarantees:
- Strict IST timezone handling (Asia/Kolkata).
- No external dependencies beyond Python standard library.
- Zero money amounts or currency symbols.
- Tenglish house style for greetings and descriptions.
"""

from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional, Tuple, Union
from zoneinfo import ZoneInfo

# Standard Indian Standard Time (UTC+5:30)
IST = ZoneInfo("Asia/Kolkata")

# ---------------------------------------------------------------------------
# Festival & Season Database (2026 - 2028)
# Sources noted per item. Single-day events use start_date == end_date.
# Multi-day events/seasons use [start_date, end_date] inclusive.
# ---------------------------------------------------------------------------

FESTIVAL_DATA: Dict[str, Dict[str, Any]] = {
    # -----------------------------------------------------------------------
    # Sankranti Festivities (Bhogi, Makara Sankranti, Kanuma)
    # Source: Solar Ingress (Makara Sankranti) & AP/Telangana State Gazettes
    # -----------------------------------------------------------------------
    "bhogi": {
        "id": "bhogi",
        "name": "Bhogi",
        "telugu_name": "భోగి",
        "category": "festival",
        "dates": {
            2026: (datetime.date(2026, 1, 14), datetime.date(2026, 1, 14)),
            2027: (datetime.date(2027, 1, 14), datetime.date(2027, 1, 14)),
            2028: (datetime.date(2028, 1, 14), datetime.date(2028, 1, 14)),
        },
        "description": "First day of Sankranti: Bhogi mantalu, discarding old things, and fresh beginnings.",
        "greeting_en": "Happy Bhogi! Wishing you warmth, joy, and prosperous new beginnings.",
        "greeting_te": "Meku me kutumba sabhulaku Bhogi subhakankshalu! Pata badhalu vadilesi kotha velugutho mundhuku sagandi.",
        "tags": ["bhogi", "sankranti", "harvest", "bonfire", "telugu festival", "bhogi mantalu"],
    },
    "sankranti": {
        "id": "sankranti",
        "name": "Makara Sankranti",
        "telugu_name": "మకర సంక్రాంతి",
        "category": "festival",
        "dates": {
            2026: (datetime.date(2026, 1, 15), datetime.date(2026, 1, 15)),
            2027: (datetime.date(2027, 1, 15), datetime.date(2027, 1, 15)),
            2028: (datetime.date(2028, 1, 15), datetime.date(2028, 1, 15)),
        },
        "description": "Main harvest festival in Andhra & Telangana: Rangoli, muggulu, haridasu, and kites.",
        "greeting_en": "Happy Makara Sankranti! May the harvest bring abundance and bright cheer.",
        "greeting_te": "Meku mariyu me kutumbaniki Makara Sankranti subhakankshalu! Ee panduga me intlo aanandam nimpali.",
        "tags": ["sankranti", "makara sankranti", "pongal", "telugu festival", "muggulu", "haridasu", "kites"],
    },
    "kanuma": {
        "id": "kanuma",
        "name": "Kanuma",
        "telugu_name": "కనుమ",
        "category": "festival",
        "dates": {
            2026: (datetime.date(2026, 1, 16), datetime.date(2026, 1, 16)),
            2027: (datetime.date(2027, 1, 16), datetime.date(2027, 1, 16)),
            2028: (datetime.date(2028, 1, 16), datetime.date(2028, 1, 16)),
        },
        "description": "Third day of Sankranti honoring cattle, agriculture, and village traditions (Pasuvula Panduga).",
        "greeting_en": "Happy Kanuma! Celebrating cattle, farmers, and the gifts of nature.",
        "greeting_te": "Meku Kanuma panduga subhakankshalu! Pasuvula panduga mariyu rythu sodharulaku vandanam.",
        "tags": ["kanuma", "sankranti", "cattle festival", "pasuvula panduga", "telugu tradition"],
    },

    # -----------------------------------------------------------------------
    # Ugadi (Telugu New Year)
    # Source: Drik Panchang Chaitra Shukla Pratipada
    # 2026: March 19, 2026 (Parabhava nama samvatsaram)
    # 2027: April 7, 2027 (Plavanga nama samvatsaram)
    # 2028: March 27, 2028 (Kilaka nama samvatsaram)
    # -----------------------------------------------------------------------
    "ugadi": {
        "id": "ugadi",
        "name": "Ugadi",
        "telugu_name": "ఉగాది",
        "category": "festival",
        "dates": {
            2026: (datetime.date(2026, 3, 19), datetime.date(2026, 3, 19)),
            2027: (datetime.date(2027, 4, 7), datetime.date(2027, 4, 7)),
            2028: (datetime.date(2028, 3, 27), datetime.date(2028, 3, 27)),
        },
        "description": "Telugu New Year day celebrating shadruchulu (Ugadi pachadi) and panchangam sravanam.",
        "greeting_en": "Happy Ugadi! May this Telugu New Year bring good health, peace, and prosperity.",
        "greeting_te": "Nutana samvatsara Ugadi subhakankshalu! Shadruchula Ugadi pachadila me jeevitham ananda mayam kavali.",
        "tags": ["ugadi", "telugu new year", "shadruchulu", "ugadi pachadi", "panchangam", "telugu festival"],
    },

    # -----------------------------------------------------------------------
    # Sri Rama Navami
    # Source: Drik Panchang Chaitra Shukla Navami (8 days after Ugadi)
    # 2026: March 27, 2026
    # 2027: April 15, 2027
    # 2028: April 4, 2028
    # -----------------------------------------------------------------------
    "sri_rama_navami": {
        "id": "sri_rama_navami",
        "name": "Sri Rama Navami",
        "telugu_name": "శ్రీరామ నవమి",
        "category": "festival",
        "dates": {
            2026: (datetime.date(2026, 3, 27), datetime.date(2026, 3, 27)),
            2027: (datetime.date(2027, 4, 15), datetime.date(2027, 4, 15)),
            2028: (datetime.date(2028, 4, 4), datetime.date(2028, 4, 4)),
        },
        "description": "Celebration of Lord Rama's birth and Sitarama Kalyanam in Bhadrachalam and Telugu homes.",
        "greeting_en": "Happy Sri Rama Navami! May the divine blessings of Lord Rama illuminate your path.",
        "greeting_te": "Sri Rama Navami subhakankshalu! Sita Rama Kalyana mahotsava subhadinana meeku shubham kalagali.",
        "tags": ["sri rama navami", "rama navami", "sitarama kalyanam", "bhadrachalam", "telugu tradition"],
    },

    # -----------------------------------------------------------------------
    # Vinayaka Chavithi (Ganesh Chaturthi)
    # Source: Drik Panchang Bhadrapada Shukla Chaturthi
    # 2026: September 14, 2026
    # 2027: September 4, 2027
    # 2028: August 24, 2028
    # -----------------------------------------------------------------------
    "vinayaka_chavithi": {
        "id": "vinayaka_chavithi",
        "name": "Vinayaka Chavithi",
        "telugu_name": "వినాయక చవితి",
        "category": "festival",
        "dates": {
            2026: (datetime.date(2026, 9, 14), datetime.date(2026, 9, 14)),
            2027: (datetime.date(2027, 9, 4), datetime.date(2027, 9, 4)),
            2028: (datetime.date(2028, 8, 24), datetime.date(2028, 8, 24)),
        },
        "description": "Festival of Lord Ganesha: Clay idol sthapana, 21 patri puja, undrallu, and visarjan.",
        "greeting_en": "Happy Vinayaka Chavithi! May Lord Ganesha remove all obstacles from your journey.",
        "greeting_te": "Vinayaka Chavithi subhakankshalu! Vigneshwarudu me panulalo vighnalu tholaginchi vijayam ivvali.",
        "tags": ["vinayaka chavithi", "ganesh chaturthi", "ganapathi puja", "patri puja", "undrallu", "modak"],
    },

    # -----------------------------------------------------------------------
    # Bathukamma (Telangana Floral Festival)
    # Source: Drik Panchang & Telangana State Gazetted Holiday (Mahalaya Amavasya to Durgashtami)
    # 2026: October 11, 2026 to October 19, 2026 (Saddula Bathukamma: Oct 19)
    # 2027: September 30, 2027 to October 8, 2027 (Saddula Bathukamma: Oct 8)
    # 2028: September 19, 2028 to September 27, 2028 (Saddula Bathukamma: Sep 27)
    # -----------------------------------------------------------------------
    "bathukamma": {
        "id": "bathukamma",
        "name": "Bathukamma",
        "telugu_name": "బతుకమ్మ",
        "category": "festival",
        "dates": {
            2026: (datetime.date(2026, 10, 11), datetime.date(2026, 10, 19)),
            2027: (datetime.date(2027, 9, 30), datetime.date(2027, 10, 8)),
            2028: (datetime.date(2028, 9, 19), datetime.date(2028, 9, 27)),
        },
        "description": "Telangana's 9-day floral festival honoring Mother Nature: Tangodu, gunugu flowers, and songs.",
        "greeting_en": "Happy Bathukamma! Celebrating blooming flowers, feminine grace, and nature's harmony.",
        "greeting_te": "Bathukamma panduga subhakankshalu! Poola panduga rojulalo me inta santosham viribuyali.",
        "tags": ["bathukamma", "telangana festival", "floral festival", "saddula bathukamma", "gunugu", "tangedu"],
    },

    # -----------------------------------------------------------------------
    # Dasara / Vijaya Dasami
    # Source: Drik Panchang Ashvina Shukla Dashami
    # 2026: October 20, 2026
    # 2027: October 10, 2027
    # 2028: September 29, 2028
    # -----------------------------------------------------------------------
    "dasara": {
        "id": "dasara",
        "name": "Dasara / Vijaya Dasami",
        "telugu_name": "దసరా / విజయదశమి",
        "category": "festival",
        "dates": {
            2026: (datetime.date(2026, 10, 20), datetime.date(2026, 10, 20)),
            2027: (datetime.date(2027, 10, 10), datetime.date(2027, 10, 10)),
            2028: (datetime.date(2028, 9, 29), datetime.date(2028, 9, 29)),
        },
        "description": "Victory of good over evil: Ayudha Puja, Shami puja, Jambi aaku, and new venture beginnings.",
        "greeting_en": "Happy Vijaya Dasami! May victory and auspicious beginnings accompany all your endeavours.",
        "greeting_te": "Vijaya Dasami / Dasara subhakankshalu! Me prathi prayatnam vijayavantham kavali ani aashistunnamu.",
        "tags": ["dasara", "vijayadashami", "navaratri", "ayudha puja", "shami puja", "victory"],
    },

    # -----------------------------------------------------------------------
    # Deepavali (Diwali)
    # Source: Drik Panchang Kartika Amavasya (Lakshmi Puja & Naraka Chaturdashi)
    # 2026: November 8, 2026
    # 2027: October 29, 2027
    # 2028: October 17, 2028
    # -----------------------------------------------------------------------
    "deepavali": {
        "id": "deepavali",
        "name": "Deepavali",
        "telugu_name": "దీపావళి",
        "category": "festival",
        "dates": {
            2026: (datetime.date(2026, 11, 8), datetime.date(2026, 11, 8)),
            2027: (datetime.date(2027, 10, 29), datetime.date(2027, 10, 29)),
            2028: (datetime.date(2028, 10, 17), datetime.date(2028, 10, 17)),
        },
        "description": "Festival of lights: Deepalu, Lakshmi pooja, sweets, and celebration of light over darkness.",
        "greeting_en": "Happy Deepavali! May the festival of lights bring brightness, peace, and happiness.",
        "greeting_te": "Deepavali panduga subhakankshalu! Deepala velugula me illu eppudu aanandam tho nindali.",
        "tags": ["deepavali", "diwali", "festival of lights", "lakshmi puja", "diyas", "deepalu"],
    },

    # -----------------------------------------------------------------------
    # Karthika Masam
    # Source: Drik Panchang Lunar Month of Kartika (starts day after Diwali Amavasya)
    # 2026: November 10, 2026 to December 9, 2026
    # 2027: October 30, 2027 to November 28, 2027
    # 2028: October 18, 2028 to November 16, 2028
    # -----------------------------------------------------------------------
    "karthika_masam": {
        "id": "karthika_masam",
        "name": "Karthika Masam",
        "telugu_name": "కార్తీక మాసం",
        "category": "festival",
        "dates": {
            2026: (datetime.date(2026, 11, 10), datetime.date(2026, 12, 9)),
            2027: (datetime.date(2027, 10, 30), datetime.date(2027, 11, 28)),
            2028: (datetime.date(2028, 10, 18), datetime.date(2028, 11, 16)),
        },
        "description": "Sacred month of spiritual discipline: Karthika somavaram fasting, deeparadhana, and vanabhojanalu.",
        "greeting_en": "Auspicious Karthika Masam! Wishing you serenity, spiritual joy, and peaceful family gatherings.",
        "greeting_te": "Pavithra Karthika Masa subhakankshalu! Deeparadhana mariyu vanabhojanala samayam lo shubham kalagali.",
        "tags": ["karthika masam", "kartika", "deeparadhana", "somavaram", "vanabhojanalu", "shiva puja"],
    },

    # -----------------------------------------------------------------------
    # Christmas
    # Source: Gregorian fixed holiday (December 25)
    # -----------------------------------------------------------------------
    "christmas": {
        "id": "christmas",
        "name": "Christmas",
        "telugu_name": "క్రిస్మస్",
        "category": "holiday",
        "dates": {
            2026: (datetime.date(2026, 12, 25), datetime.date(2026, 12, 25)),
            2027: (datetime.date(2027, 12, 25), datetime.date(2027, 12, 25)),
            2028: (datetime.date(2028, 12, 25), datetime.date(2028, 12, 25)),
        },
        "description": "Christmas celebration of peace, family reunions, gift exchanges, and warm winter cheer.",
        "greeting_en": "Merry Christmas! Wishing you and your loved ones peace, warmth, and festive cheer.",
        "greeting_te": "Christmas panduga subhakankshalu! Ee shubhadinam me intlo prema, shanti nimpali.",
        "tags": ["christmas", "xmas", "winter holiday", "gifting", "celebration", "family"],
    },

    # -----------------------------------------------------------------------
    # New Year
    # Source: Gregorian fixed holiday (January 1)
    # -----------------------------------------------------------------------
    "new_year": {
        "id": "new_year",
        "name": "New Year's Day",
        "telugu_name": "నూతన సంవత్సర దినోత్సవం",
        "category": "holiday",
        "dates": {
            2026: (datetime.date(2026, 1, 1), datetime.date(2026, 1, 1)),
            2027: (datetime.date(2027, 1, 1), datetime.date(2027, 1, 1)),
            2028: (datetime.date(2028, 1, 1), datetime.date(2028, 1, 1)),
        },
        "description": "Global New Year day: Fresh start, resolutions, habit resets, and annual planning.",
        "greeting_en": "Happy New Year! May this year bring clarity, steady growth, and renewed energy.",
        "greeting_te": "Nutana Samvatsara subhakankshalu! Ee kotha samvatsaram me sankalpalanu neraverchali.",
        "tags": ["new year", "january 1", "fresh start", "resolutions", "goal setting", "annual reset"],
    },

    # -----------------------------------------------------------------------
    # Exam Seasons
    # Sources:
    # India: CBSE / Andhra / Telangana SSC & Inter Board Exam window (mid-Feb to mid-April)
    # US: Spring Semester Finals / AP Exam window (mid-April to late-May)
    # -----------------------------------------------------------------------
    "exam_season_india": {
        "id": "exam_season_india",
        "name": "Exam Season (India)",
        "telugu_name": "పరీక్షల సమయం (భారత్)",
        "category": "season",
        "dates": {
            2026: (datetime.date(2026, 2, 15), datetime.date(2026, 4, 15)),
            2027: (datetime.date(2027, 2, 15), datetime.date(2027, 4, 15)),
            2028: (datetime.date(2028, 2, 15), datetime.date(2028, 4, 15)),
        },
        "description": "Indian board and university examination window: Revision schedules, study focus, and mock tests.",
        "greeting_en": "Best wishes for exam season! Stay focused, calm, and prepared.",
        "greeting_te": "Parikshala samayam lo vidyarthulaku all the best! Manchi concentration tho prepare avvandi.",
        "tags": ["exam season", "board exams", "revision", "study timetable", "student focus", "cbse", "ssc"],
    },
    "exam_season_us": {
        "id": "exam_season_us",
        "name": "Exam Season (US Finals)",
        "telugu_name": "పరీక్షల సమయం (యూఎస్ ఫైనల్స్)",
        "category": "season",
        "dates": {
            2026: (datetime.date(2026, 4, 20), datetime.date(2026, 5, 25)),
            2027: (datetime.date(2027, 4, 20), datetime.date(2027, 5, 25)),
            2028: (datetime.date(2028, 4, 20), datetime.date(2028, 5, 25)),
        },
        "description": "US high school and college Spring finals & AP testing period.",
        "greeting_en": "Wishing students clear focus and steady confidence during finals week.",
        "greeting_te": "Spring finals ki all the best! Quiet study routine tho calm ga undandi.",
        "tags": ["finals", "exam prep", "college exams", "ap exams", "study tracker", "revision log"],
    },

    # -----------------------------------------------------------------------
    # Back to School Seasons
    # Sources:
    # India: School reopening after summer vacation (June 1 - July 15)
    # US: Fall school reopening (August 1 - September 15)
    # -----------------------------------------------------------------------
    "back_to_school_india": {
        "id": "back_to_school_india",
        "name": "Back to School (India)",
        "telugu_name": "బడి పునఃప్రారంభం (భారత్)",
        "category": "season",
        "dates": {
            2026: (datetime.date(2026, 6, 1), datetime.date(2026, 7, 15)),
            2027: (datetime.date(2027, 6, 1), datetime.date(2027, 7, 15)),
            2028: (datetime.date(2028, 6, 1), datetime.date(2028, 7, 15)),
        },
        "description": "Indian academic year reopening: Timetables, notebook covers, teacher planners, and school readiness.",
        "greeting_en": "Wishing students and teachers a smooth, joyful return to school!",
        "greeting_te": "Kotha academic year ki welcome! Pillalu mariyu teachers ki exciting start kavali.",
        "tags": ["back to school", "academic year", "school timetable", "lesson planner", "student organizer"],
    },
    "back_to_school_us": {
        "id": "back_to_school_us",
        "name": "Back to School (US)",
        "telugu_name": "బడి పునఃప్రారంభం (యూఎస్)",
        "category": "season",
        "dates": {
            2026: (datetime.date(2026, 8, 1), datetime.date(2026, 9, 15)),
            2027: (datetime.date(2027, 8, 1), datetime.date(2027, 9, 15)),
            2028: (datetime.date(2028, 8, 1), datetime.date(2028, 9, 15)),
        },
        "description": "US Fall school reopening: Homework trackers, classroom decor, homeschool schedules, and teacher binders.",
        "greeting_en": "Have a wonderful, inspiring start to the new school year!",
        "greeting_te": "New school year ki best wishes! Classroom routines mariyu schedules set cheskondi.",
        "tags": ["back to school", "fall semester", "student binder", "teacher planner", "homework tracker"],
    },

    # -----------------------------------------------------------------------
    # Tax Seasons
    # Sources:
    # US: IRS annual federal tax filing season (January 15 to April 15)
    # India: Income Tax Department individual non-audit ITR window (June 1 to July 31)
    # -----------------------------------------------------------------------
    "tax_season_us": {
        "id": "tax_season_us",
        "name": "Tax Season (US)",
        "telugu_name": "పన్ను సమయం (యూఎస్)",
        "category": "season",
        "dates": {
            2026: (datetime.date(2026, 1, 15), datetime.date(2026, 4, 15)),
            2027: (datetime.date(2027, 1, 15), datetime.date(2027, 4, 15)),
            2028: (datetime.date(2028, 1, 15), datetime.date(2028, 4, 15)),
        },
        "description": "US federal and state income tax filing preparation window for individuals and small businesses.",
        "greeting_en": "Stay organized this tax season with orderly records and stress-free document tracking.",
        "greeting_te": "Tax season lo receipt tracking mariyu document organization baga maintain cheyandi.",
        "tags": ["tax season", "receipt organizer", "mileage tracker", "freelance bookkeeping", "tax preparation"],
    },
    "tax_season_india": {
        "id": "tax_season_india",
        "name": "Tax Season (India)",
        "telugu_name": "పన్ను సమయం (భారత్)",
        "category": "season",
        "dates": {
            2026: (datetime.date(2026, 6, 1), datetime.date(2026, 7, 31)),
            2027: (datetime.date(2027, 6, 1), datetime.date(2027, 7, 31)),
            2028: (datetime.date(2028, 6, 1), datetime.date(2028, 7, 31)),
        },
        "description": "Indian financial year assessment and ITR filing season for salaried employees and professionals.",
        "greeting_en": "Smooth and timely tax filing with organized financial records and deduction sheets.",
        "greeting_te": "India ITR filing samayam lo accounts mariyu tax documents ready ga unchandi.",
        "tags": ["tax season india", "itr filing", "investment declarations", "salary slips", "tax checklist"],
    },
}

SUPPORTED_YEARS = (2026, 2027, 2028)


def _to_ist_date(target: Optional[Union[datetime.date, datetime.datetime]] = None) -> datetime.date:
    """Normalize input date or datetime to an IST date.

    If target is None, returns current date in IST.
    If target is a datetime:
      - If naive (no tzinfo), treat as UTC and convert to IST.
      - If tz-aware, convert directly to IST.
    If target is already a date (not datetime), returns it directly.
    """
    if target is None:
        return datetime.datetime.now(IST).date()

    if isinstance(target, datetime.datetime):
        if target.tzinfo is None:
            # Assume UTC for naive datetime, then convert to IST
            target = target.replace(tzinfo=datetime.timezone.utc)
        return target.astimezone(IST).date()

    if isinstance(target, datetime.date):
        return target

    raise TypeError(f"Expected datetime.date or datetime.datetime, got {type(target).__name__}")


def get_festival(festival_id: str) -> Optional[Dict[str, Any]]:
    """Return dictionary of festival metadata by ID, or None if not found."""
    if not festival_id or not isinstance(festival_id, str):
        return None
    fid = festival_id.strip().lower()
    return FESTIVAL_DATA.get(fid)


def all_festivals() -> List[Dict[str, Any]]:
    """Return list of all registered festivals and seasonal windows."""
    return [dict(f) for f in FESTIVAL_DATA.values()]


def is_festival(
    target_date: Optional[Union[datetime.date, datetime.datetime]] = None,
    category: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Check if the given date (default: today IST) matches any festival or seasonal window.

    Returns a list of matching festival dictionaries, including an extra 'active_year'
    and 'is_single_day' flag. If no festival matches, returns an empty list.

    Parameters:
      target_date: date or datetime to check (interpreted in IST).
      category: optional filter by category ('festival', 'holiday', 'season').
    """
    cur_date = _to_ist_date(target_date)
    year = cur_date.year

    matches: List[Dict[str, Any]] = []

    # Check the target year and adjacent years (in case of year-boundary span)
    check_years = [y for y in (year - 1, year, year + 1) if y in SUPPORTED_YEARS]

    for fid, meta in FESTIVAL_DATA.items():
        if category and meta.get("category") != category:
            continue

        for y in check_years:
            if y not in meta["dates"]:
                continue
            start, end = meta["dates"][y]
            if start <= cur_date <= end:
                entry = dict(meta)
                entry["active_year"] = y
                entry["start_date"] = start
                entry["end_date"] = end
                entry["is_single_day"] = (start == end)
                matches.append(entry)
                break

    return matches


def upcoming(
    now: Optional[Union[datetime.date, datetime.datetime]] = None,
    days: int = 30,
    category: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return festivals and seasons starting or active within the next `days` from `now` (IST).

    Results are sorted chronologically by start date, then by end date.
    Each returned item is enriched with:
      - 'start_date': date the event starts
      - 'end_date': date the event ends
      - 'days_until_start': integer days until start (negative if already in progress)
      - 'days_until_end': integer days until end
      - 'is_in_progress': boolean True if currently active
      - 'is_single_day': boolean True if 1-day event

    Parameters:
      now: base date or datetime (default: today IST)
      days: lookahead window in days (default: 30)
      category: optional category filter ('festival', 'holiday', 'season')
    """
    base_date = _to_ist_date(now)
    cutoff_date = base_date + datetime.timedelta(days=max(0, days))

    results: List[Dict[str, Any]] = []

    for fid, meta in FESTIVAL_DATA.items():
        if category and meta.get("category") != category:
            continue

        for y, (start, end) in meta["dates"].items():
            # Included if it overlaps with [base_date, cutoff_date]:
            # end >= base_date AND start <= cutoff_date
            if end >= base_date and start <= cutoff_date:
                entry = dict(meta)
                entry["active_year"] = y
                entry["start_date"] = start
                entry["end_date"] = end
                entry["days_until_start"] = (start - base_date).days
                entry["days_until_end"] = (end - base_date).days
                entry["is_in_progress"] = (start <= base_date <= end)
                entry["is_single_day"] = (start == end)
                results.append(entry)

    # Sort primarily by start_date, then end_date, then festival name
    results.sort(key=lambda x: (x["start_date"], x["end_date"], x["name"]))
    return results


def greeting_te(festival: Union[str, Dict[str, Any]]) -> str:
    """Return the owner-facing Tenglish greeting for a festival.

    Accepts either a festival ID string (e.g. 'ugadi', 'sankranti') or
    a festival metadata dictionary.

    Returns a friendly, culturally resonant Tenglish greeting string.
    If the festival is unknown, returns a generic cheerful Tenglish greeting.
    """
    if not festival:
        return "Panduga subhakankshalu! Me kutumbaniki shubham kalagali."

    if isinstance(festival, str):
        meta = get_festival(festival)
    elif isinstance(festival, dict):
        fid = festival.get("id")
        meta = get_festival(fid) if fid else festival
    else:
        return "Panduga subhakankshalu! Me kutumbaniki shubham kalagali."

    if meta and "greeting_te" in meta:
        return meta["greeting_te"]

    # Fallback greeting
    name = (meta.get("name") if meta else None) or str(festival)
    return f"{name} subhakankshalu! Me kutumbaniki shubham kalagali."
