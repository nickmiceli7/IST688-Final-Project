"""Pure time functions for the Checker: read the Host's "when" text and test Google hours.

No network and no clock calls: every function takes `now` so tests can fix the time.
Google's day numbers run 0 = Sunday ... 6 = Saturday; Python's weekday() runs 0 = Monday.
"""
import re
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/New_York")

DAYS = {"monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1, "wednesday": 2, "wed": 2,
        "thursday": 3, "thu": 3, "thur": 3, "thurs": 3, "friday": 4, "fri": 4,
        "saturday": 5, "sat": 5, "sunday": 6, "sun": 6}
DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

_TIME_RE = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)?(?![\w:])")


# ---------- the Host's "when" text ----------
def _parse_clock(text, evening):
    """Find a clock time in text. Returns (time, assumed_pm) or (None, False)."""
    if re.search(r"\bnoon\b", text):
        return time(12, 0), False
    if re.search(r"\bmidnight\b", text):
        return time(23, 59), False
    for m in _TIME_RE.finditer(text):
        hour, minute = int(m.group(1)), int(m.group(2) or 0)
        ampm = (m.group(3) or "").replace(".", "")
        if minute > 59 or hour > 23:
            continue
        if ampm:
            if not 1 <= hour <= 12:
                continue
            hour = hour % 12 + (12 if ampm == "pm" else 0)
            return time(hour, minute), False
        if hour >= 13 or hour == 0 or m.group(2) and hour >= 12:
            return time(hour, minute), False        # 24-hour clock, e.g. "19:00"
        if hour == 12:
            return time(12, minute), False
        # A bare "7" or "8": restaurants mean evening, so read 1-10 as PM (11 stays AM).
        if evening or 1 <= hour <= 10:
            return time(hour + 12, minute), not evening
        return time(hour, minute), False
    return None, False


def parse_when(text, now):
    """Turn the Host's free-text "when" into a datetime in America/New_York.

    Returns (datetime or None, note). The note says how the text was read, or why no time
    could be checked. Handles "Friday 7 PM", "Fri 7pm", "tonight 8", "tomorrow at noon",
    "now", "Saturday 19:00" and blank. A day is the next one with that name (today counts
    if the time hasn't passed yet).
    """
    raw = (text or "").strip()
    t = raw.lower()
    if not t:
        return None, "No day or time was given, so opening hours were not checked against a time."
    if re.fullmatch(r"(right )?now|asap|as soon as possible", t):
        return now, f"Checked for now ({describe_time(now)})."

    day_offset, weekday = None, None
    if re.search(r"\b(today|tonight)\b", t):
        day_offset = 0
    elif re.search(r"\btomorrow\b", t):
        day_offset = 1
    else:
        for word in re.findall(r"[a-z]+", t):
            if word in DAYS:
                weekday = DAYS[word]
                break

    clock, assumed_pm = _parse_clock(t, evening="tonight" in t)
    if clock is None:
        if day_offset is not None or weekday is not None:
            return None, f'No time was given in "{raw}", so opening hours were not checked against a time.'
        return None, f'Could not read the time "{raw}", so opening hours were not checked against a time.'

    if day_offset is not None:
        date = now.date() + timedelta(days=day_offset)
    elif weekday is not None:
        ahead = (weekday - now.weekday()) % 7
        date = now.date() + timedelta(days=ahead)
        if ahead == 0 and clock < now.time():
            date += timedelta(days=7)
    else:                                               # time only: the next time it comes round
        date = now.date()
        if clock < now.time():
            date += timedelta(days=1)

    when = datetime.combine(date, clock, tzinfo=TZ)
    note = f"Checked for {describe_time(when)}."
    if assumed_pm:
        note += " A time without AM or PM was read as PM."
    return when, note


def describe_time(dt):
    """'Friday at 7 PM' / 'Friday at 7:30 PM'."""
    hour = dt.hour % 12 or 12
    clock = f"{hour}" if dt.minute == 0 else f"{hour}:{dt.minute:02d}"
    return f"{DAY_NAMES[dt.weekday()]} at {clock} {'AM' if dt.hour < 12 else 'PM'}"


# ---------- Google opening hours ----------
def _google_to_python_day(day):
    return (day - 1) % 7


def _point_on_week(point, week_monday):
    days = _google_to_python_day(point["day"])
    return week_monday + timedelta(days=days, hours=point.get("hour", 0), minutes=point.get("minute", 0))


def _point_on_date(point):
    d = point["date"]
    return datetime(d["year"], d["month"], d["day"], point.get("hour", 0), point.get("minute", 0), tzinfo=TZ)


def intervals_around(periods, dt):
    """Concrete (start, end) datetimes for Google periods near dt.

    Periods with a "date" (currentOpeningHours) are used as dated. Weekly periods
    (regularOpeningHours) are laid on the week before, of and after dt, so a period that
    opens Saturday 5 PM and closes Sunday 2 AM covers both sides of midnight.
    """
    out = []
    week_monday = datetime.combine(dt.date() - timedelta(days=dt.weekday()), time(0), tzinfo=dt.tzinfo)
    for p in periods:
        start_pt, end_pt = p.get("open"), p.get("close")
        if not start_pt or not end_pt:
            continue
        if "date" in start_pt and "date" in end_pt:
            out.append((_point_on_date(start_pt), _point_on_date(end_pt)))
            continue
        for weeks in (-1, 0, 1):
            base = week_monday + timedelta(weeks=weeks)
            start = _point_on_week(start_pt, base)
            end = _point_on_week(end_pt, base)
            if end <= start:                    # runs past Saturday night into Sunday
                end += timedelta(days=7)
            out.append((start, end))
    return out


def always_open(periods):
    """Google writes 24/7 as one period that opens Sunday 00:00 and has no close."""
    return any(p.get("open") and not p.get("close") for p in periods)


def is_open_at(periods, dt):
    """True/False from Google periods, or None when Google lists no hours."""
    if not periods:
        return None
    if always_open(periods):
        return True
    return any(start <= dt < end for start, end in intervals_around(periods, dt))


def open_at(place, dt, now):
    """Open at dt? Uses currentOpeningHours (holiday-aware, next 7 days) when dt falls inside
    its dated periods' week, otherwise regularOpeningHours. None if no hours are listed."""
    current = place.get("current_periods") or []
    dated = [p for p in current if "date" in (p.get("open") or {})]
    if dated and now <= dt < now + timedelta(days=6):
        return is_open_at(current, dt)
    return is_open_at(place.get("regular_periods") or [], dt)


def hours_today(hours_text, now):
    """Google's weekdayDescriptions start on Monday. Returns today's line or ''."""
    if not hours_text or len(hours_text) < 7:
        return ""
    return hours_text[now.weekday()]
