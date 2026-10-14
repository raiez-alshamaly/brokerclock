"""An independent reference for the tests: the published daylight-saving rules, written out by hand.

Nothing here uses zoneinfo or brokerclock, so the tests compare the library with the rules themselves.
All times are integer epoch seconds; "server" times are the broker's wall clock written as epoch seconds.
"""

import calendar
from datetime import date, timedelta
from functools import cache

MINUTE = 60
HOUR = 3600
DAY = 86400
STEP = 5 * MINUTE  # synthetic markets trade in 5-minute steps; every clock offset is a multiple of this

_EPOCH_DAY = date(1970, 1, 1)


def epoch(day: date, hour: int = 0, minute: int = 0) -> int:
    """UTC epoch seconds of a calendar date and time read as UTC."""
    return calendar.timegm((day.year, day.month, day.day, hour, minute, 0))


def _nth_sunday(year: int, month: int, n: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(6 - first.weekday()) % 7, weeks=n - 1)


def _last_sunday(year: int, month: int) -> date:
    last = date(year + month // 12, month % 12 + 1, 1) - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - 6) % 7)


@cache
def switches(rule: str, year: int) -> tuple[int, int]:
    """UTC instants when daylight saving starts and ends in ``year``."""
    if rule == "US":  # second Sunday of March 02:00 EST, first Sunday of November 02:00 EDT
        return epoch(_nth_sunday(year, 3, 2), 7), epoch(_nth_sunday(year, 11, 1), 6)
    if rule == "EU":  # last Sunday of March, last Sunday of October, both 01:00 UTC
        return epoch(_last_sunday(year, 3), 1), epoch(_last_sunday(year, 10), 1)
    if rule == "AU":  # first Sunday of October 02:00 AEST, first Sunday of April 03:00 AEDT: 16:00 UTC the day before
        start = _nth_sunday(year, 10, 1) - timedelta(days=1)
        end = _nth_sunday(year, 4, 1) - timedelta(days=1)
        return epoch(start, 16), epoch(end, 16)
    raise ValueError(rule)


def in_daylight_saving(rule: str, t: int) -> bool:
    if rule == "fixed":
        return False
    start, end = switches(rule, (_EPOCH_DAY + timedelta(seconds=t)).year)
    if rule == "AU":  # southern summer spans the new year
        return t >= start or t < end
    return start <= t < end


def offset(rule: str, base_hours: float, t: int) -> int:
    """The server's UTC offset in seconds at UTC instant ``t``: base, plus an hour in daylight saving."""
    return round(base_hours * HOUR) + (HOUR if in_daylight_saving(rule, t) else 0)


def new_york(day: date, hour: int, minute: int = 0) -> int:
    """UTC instant of a New York wall time away from the 02:00 switch hour."""
    local = epoch(day, hour, minute)
    summer = local + 4 * HOUR
    return summer if in_daylight_saving("US", summer) else local + 5 * HOUR


def forex_bars(
    rule: str,
    base_hours: float,
    first_sunday: date,
    weeks: int,
    *,
    timeframe: int = HOUR,
    close: tuple[int, int] = (17, 0),
    daily_break: bool = False,
    skip: frozenset[date] = frozenset(),
    early_closes: dict[date, tuple[date, int, int]] | None = None,
) -> list[int]:
    """Bar open times (server epoch) of a forex major on a broker whose clock is ``rule`` plus ``base_hours``.

    Each week opens on Sunday 17:00 New York and closes on Friday at ``close`` New York. ``daily_break`` stops
    trading from 17:00 to 17:05 New York every day. ``skip`` holds the Sundays of weeks with no data, and
    ``early_closes`` maps a week's Sunday to the New York (date, hour, minute) when that week closes instead.
    """
    early_closes = early_closes or {}
    bars = set()
    for n in range(weeks):
        sunday = first_sunday + timedelta(weeks=n)
        if sunday in skip:
            continue
        start = new_york(sunday, 17)
        end = new_york(*early_closes.get(sunday, (sunday + timedelta(days=5), *close)))
        for t in range(start, end, min(STEP, timeframe)):
            if daily_break and _in_daily_break(t):
                continue
            server = t + offset(rule, base_hours, t)
            bars.add(server - server % timeframe)
    return sorted(bars)


def _in_daily_break(t: int) -> bool:
    new_york_wall = t - 5 * HOUR + (HOUR if in_daylight_saving("US", t) else 0)
    return 17 * HOUR <= new_york_wall % DAY < 17 * HOUR + 5 * MINUTE
