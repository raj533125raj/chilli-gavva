"""Deterministic daily publishing times in India Standard Time."""
import datetime
import random
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
PINTEREST_WINDOWS = ((10, 0, 12, 0), (15, 0, 17, 0), (20, 0, 22, 0))
TELEGRAM_WINDOWS = ((9, 30, 11, 30), (18, 0, 20, 0))


def _ist(now):
    if now is None:
        now = datetime.datetime.now(datetime.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)
    return now.astimezone(IST)


def _minute_in_window(day, label, window):
    start = window[0] * 60 + window[1]
    end = window[2] * 60 + window[3]
    # The final minute is 11:59 for a 10:00-12:00 window.
    minute = random.Random(f"r23:{day.isoformat()}:{label}:{window}").randrange(start, end)
    return datetime.datetime.combine(day, datetime.time(minute // 60, minute % 60), IST)


def daily_slots(day):
    """Return the same random-looking, one-minute slots every time for an IST day."""
    if isinstance(day, datetime.datetime):
        day = _ist(day).date()
    return {
        "pinterest": [_minute_in_window(day, "pinterest", window) for window in PINTEREST_WINDOWS],
        "telegram": [_minute_in_window(day, "telegram", window) for window in TELEGRAM_WINDOWS],
    }


def blocked(now=None):
    """No publishing in the daily rest or no-commit window."""
    local = _ist(now)
    minute = local.hour * 60 + local.minute
    return 6 * 60 <= minute < 6 * 60 + 6 or 75 <= minute < 195


def due(publish_type, now=None):
    """Whether this type may run at this moment; Gumroad has no daily timer."""
    if publish_type not in ("gumroad", "pinterest", "telegram"):
        return False
    local = _ist(now)
    if blocked(local):
        return False
    if publish_type == "gumroad":
        return True
    return any(local.replace(second=0, microsecond=0) == slot for slot in daily_slots(local.date())[publish_type])
