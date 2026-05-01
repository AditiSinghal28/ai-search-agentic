from __future__ import annotations

import calendar
import re
from datetime import date, timedelta


MONTHS = {
    "january": 1,
    "jan": 1,
    "february": 2,
    "feb": 2,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "may": 5,
    "june": 6,
    "jun": 6,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sep": 9,
    "october": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dec": 12,
}


WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


def start_of_week(d: date) -> date:
    return d - timedelta(days=d.weekday())


def end_of_week(d: date) -> date:
    return start_of_week(d) + timedelta(days=6)


def start_of_month(d: date) -> date:
    return d.replace(day=1)


def end_of_month(d: date) -> date:
    if d.month == 12:
        next_month = d.replace(year=d.year + 1, month=1, day=1)
    else:
        next_month = d.replace(month=d.month + 1, day=1)
    return next_month - timedelta(days=1)


def previous_week_range(today: date) -> tuple[date, date]:
    this_week_start = start_of_week(today)
    prev_week_end = this_week_start - timedelta(days=1)
    prev_week_start = prev_week_end - timedelta(days=6)
    return prev_week_start, prev_week_end


def previous_month_range(today: date) -> tuple[date, date]:
    first_day_this_month = today.replace(day=1)
    last_day_prev_month = first_day_this_month - timedelta(days=1)
    first_day_prev_month = last_day_prev_month.replace(day=1)
    return first_day_prev_month, last_day_prev_month


def extract_weekday(query: str) -> str | None:
    q = query.lower().strip()

    for day in WEEKDAYS:
        if re.search(rf"\b{day}s?\b", q):
            return day

    return None


def resolve_next_weekday(today: date, weekday_name: str) -> date:
    """
    Return the next occurrence of a weekday, including today if it matches.
    """
    target_weekday = WEEKDAYS[weekday_name.lower()]
    days_ahead = (target_weekday - today.weekday()) % 7
    return today + timedelta(days=days_ahead)


def resolve_previous_weekday(today: date, weekday_name: str) -> date:
    """
    Return the previous occurrence of a weekday, including today if it matches.
    """
    target_weekday = WEEKDAYS[weekday_name.lower()]
    days_back = (today.weekday() - target_weekday) % 7
    return today - timedelta(days=days_back)


def detect_date_range(query: str, today: date) -> tuple[date | None, date | None, str | None]:
    q = query.lower()

    for month_name, month_num in MONTHS.items():
        if re.search(rf"\b{month_name}\b", q):
            year_match = re.search(r"\b(20\d{2})\b", q)
            year = int(year_match.group(1)) if year_match else today.year

            start = date(year, month_num, 1)
            last_day = calendar.monthrange(year, month_num)[1]
            end = date(year, month_num, last_day)

            return start, end, month_name.capitalize()
        
    q = query.lower().strip()

    if "today" in q:
        return today, today, "today"

    if "yesterday" in q:
        y = today - timedelta(days=1)
        return y, y, "yesterday"

    if "this week" in q or "current week" in q:
        return start_of_week(today), end_of_week(today), "this week"

    if "last week" in q or "past week" in q:
        start, end = previous_week_range(today)
        return start, end, "last week"

    if "this month" in q or "current month" in q:
        return start_of_month(today), end_of_month(today), "this month"

    if "last month" in q or "past month" in q:
        start, end = previous_month_range(today)
        return start, end, "last month"

    if "this year" in q or "current year" in q:
        return date(today.year, 1, 1), date(today.year, 12, 31), "this year"

    match = re.search(r"(?:last|past)\s+(\d+)\s+days?", q)
    if match:
        days = int(match.group(1))
        return today - timedelta(days=days - 1), today, f"last {days} days"

    match = re.search(r"(?:last|past)\s+(\d+)\s+weeks?", q)
    if match:
        weeks = int(match.group(1))
        return today - timedelta(days=(weeks * 7) - 1), today, f"last {weeks} weeks"

    match = re.search(r"(?:last|past)\s+(\d+)\s+months?", q)
    if match:
        months = int(match.group(1))
        return today - timedelta(days=(months * 30) - 1), today, f"last {months} months"

    # Specific weekday references
    weekday = extract_weekday(q)
    if weekday:
        if re.search(rf"\blast\s+{weekday}s?\b", q):
            target = resolve_previous_weekday(today - timedelta(days=7), weekday)
            return target, target, f"last {weekday}"

        if re.search(rf"\bnext\s+{weekday}s?\b", q):
            target = resolve_next_weekday(today + timedelta(days=1), weekday)
            return target, target, f"next {weekday}"

        # Plain "Monday" / "Mondays" should usually act as a weekday filter, not a single date
        return None, None, None

    return None, None, None