"""Find a broker's clock rule from the weekly closes in its price history, without the PC clock."""

from __future__ import annotations

import math
import numbers
import operator
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from itertools import pairwise
from zoneinfo import ZoneInfo

from brokerclock._errors import NotEnoughData
from brokerclock._zone import _EPOCH, _MAX_OFFSET, _MIN_OFFSET, _QUARTER_HOUR, _RULES, ServerZone

_NEW_YORK = ZoneInfo("America/New_York")
_WEEKLY_CLOSE = time(17)  # Friday 17:00 New York ends the forex week
_FRIDAY = 4
_WEEKEND_GAP = timedelta(hours=40)
_LATE_SLACK = timedelta(seconds=60)  # a last bar may end a few seconds after 17:00 (a late tick, a fast clock)
_MIN_TIMEFRAME = 60
_MAX_TIMEFRAME = 3600
_MIN_WEEKS = 2


@dataclass(frozen=True)
class WeekClose:
    """One weekly close found in the bars.

    ``friday_close_utc`` is the Friday 17:00 New York instant (aware UTC), ``server_close`` the close on the server
    clock in epoch seconds (last bar open + timeframe), and ``offset`` their difference rounded up to 15 minutes.
    """

    friday_close_utc: datetime
    server_close: int
    offset: timedelta


@dataclass(frozen=True)
class Detection:
    """The result of :func:`detect`.

    ``zone`` is the best-scoring server clock, or None when several clocks score the same (``candidates`` then
    holds all of them). ``matched`` of ``total`` weeks agree with it; ``outliers`` are the weeks it does not
    predict (for example early holiday closes), and ``unexplained`` the server close times that fit no Friday.
    """

    zone: ServerZone | None
    candidates: tuple[ServerZone, ...]
    weeks: tuple[WeekClose, ...]
    matched: int
    total: int
    unexplained: tuple[int, ...]
    outliers: tuple[WeekClose, ...]

    @property
    def ambiguous(self) -> bool:
        """True when the data cannot tell the candidates apart."""
        return self.zone is None


def detect(bar_times: Iterable[int], timeframe: int = 3600) -> Detection:
    """Find the server clock from the open times of a forex major's bars.

    ``bar_times`` are server epoch seconds (the ``time`` column of ``copy_rates_*``) in any order; duplicates are
    ignored. ``timeframe`` is the bar length in seconds, from 60 (M1) to 3600 (H1).

    Each gap of 40 hours or more after a bar marks a weekly close at that bar's open + ``timeframe``. The forex
    week ends on Friday at 17:00 New York, so each close gives the server's offset in that week. Each candidate
    clock (fixed, US, EU or Australian daylight saving, any base offset) scores the weeks it predicts, and the
    best wins. A tie means the data cannot tell the clocks apart.

    What the data needs:

    * a forex major such as EURUSD; metals and indices follow their exchange's hours;
    * at least six months of H1 bars, or a span that includes March or late October to early November, when the
      US and EU switch on different dates. Summer-only data cannot separate US from EU rules, and data without a
      switch cannot separate a fixed clock from a switching one;
    * bars of one hour or shorter. The close is taken as the last bar's open + ``timeframe``, so it is exact only
      when a bar ends at the weekly close; longer bars end on the server's 4-hour or day boundaries, which hide
      the switch weeks, so they are rejected. Use M15 or shorter for clocks with 30- or 45-minute offsets.

    Running it on two majors and comparing the results is good practice.

    A broker's week ends at or before 17:00 New York, never after, so each offset is rounded up to the next 15
    minutes (after allowing 60 seconds for a bar that ends just past 17:00). Closes less than 14 minutes early
    (16:50 in M5 bars, 16:47 or later in M1 bars) are read correctly. A week that ends 14 minutes or more early
    (16:40, say) reads 15 minutes off: if only some weeks do that they become ``outliers``; if every week does,
    the whole result is 15 minutes off, for example GMT+1:45/+2:45 instead of GMT+2/+3. H1 bars are not
    affected, because the last H1 bar still ends at 17:00.

    Raises ``NotEnoughData`` when fewer than two weekly closes match a Friday.

    >>> monday = 1736121600  # 2025-01-06 00:00 on a GMT+2 server, which is Sunday 17:00 New York
    >>> bars = [monday + week * 7 * 86400 + hour * 3600 for week in range(3) for hour in range(120)]
    >>> result = detect(bars)  # three weeks of H1 bars: two weekly closes
    >>> result.total, result.weeks[0].offset
    (2, datetime.timedelta(seconds=7200))
    >>> result.ambiguous  # January only: no switch, so several clocks fit
    True
    >>> [str(zone) for zone in result.candidates]
    ['GMT+2/+3 (US daylight saving)', 'GMT+2/+3 (EU daylight saving)', 'GMT+1/+2 (AU daylight saving)', 'GMT+2 (fixed)']
    """
    if isinstance(timeframe, bool) or not isinstance(timeframe, numbers.Integral):
        raise ValueError(f"timeframe must be a whole number of seconds, got {timeframe!r}")
    if not _MIN_TIMEFRAME <= timeframe <= _MAX_TIMEFRAME:
        raise ValueError(f"timeframe must be between 60 and 3600 seconds (M1 to H1), got {timeframe}")
    weeks: list[WeekClose] = []
    unexplained: list[int] = []
    for close in _weekly_closes(bar_times, int(timeframe)):
        week = _week_close(close)
        if week is None:
            unexplained.append(close)
        else:
            weeks.append(week)
    if len(weeks) < _MIN_WEEKS:
        raise NotEnoughData(
            f"found {len(weeks)} weekly close(s) that match a Friday ({len(unexplained)} more do not); "
            f"need {_MIN_WEEKS}"
        )
    candidates, matched = _best_zones(weeks)
    outliers = tuple(w for w in weeks if all(z.offset_at(w.friday_close_utc) != w.offset for z in candidates))
    return Detection(
        zone=candidates[0] if len(candidates) == 1 else None,
        candidates=candidates,
        weeks=tuple(weeks),
        matched=matched,
        total=len(weeks),
        unexplained=tuple(unexplained),
        outliers=outliers,
    )


def _weekly_closes(bar_times: Iterable[int], timeframe: int) -> list[int]:
    """Server close times of the bars followed by a weekend-sized gap."""
    gap = int(_WEEKEND_GAP.total_seconds())
    times = sorted({operator.index(t) for t in bar_times})
    return [bar + timeframe for bar, following in pairwise(times) if following - (bar + timeframe) >= gap]


def _week_close(server_close: int) -> WeekClose | None:
    """Match a server close to the Friday 17:00 New York that puts it within -12 h..+14 h, if there is one."""
    wall = _EPOCH + timedelta(seconds=server_close)
    # Within that range the server's date is the Friday itself or the Saturday after it.
    for days_back in (0, 1):
        day = wall.date() - timedelta(days=days_back)
        if day.weekday() != _FRIDAY:
            continue
        friday = datetime.combine(day, _WEEKLY_CLOSE, tzinfo=_NEW_YORK).astimezone(timezone.utc)
        offset = wall - friday.replace(tzinfo=None)
        if _MIN_OFFSET <= offset <= _MAX_OFFSET:
            return WeekClose(friday, server_close, _round_up_quarter(offset))
    return None


def _round_up_quarter(offset: timedelta) -> timedelta:
    """Round a measured weekly-close offset up to 15 minutes: brokers close at or before 17:00, never after."""
    return _QUARTER_HOUR * math.ceil((offset - _LATE_SLACK) / _QUARTER_HOUR)


def _best_zones(weeks: list[WeekClose]) -> tuple[tuple[ServerZone, ...], int]:
    """The clocks that predict the most weeks, and how many weeks that is."""
    votes: Counter[ServerZone] = Counter()
    for week in weeks:
        for key, standard in _RULES.values():
            tz = ZoneInfo(key)
            daylight = tz.dst(week.friday_close_utc.astimezone(tz))
            zone = _valid_zone(key, week.offset - daylight - standard)
            if zone is not None:
                votes[zone] += 1
    best = max(votes.values())
    return tuple(zone for zone, count in votes.items() if count == best), best


def _valid_zone(key: str, shift: timedelta) -> ServerZone | None:
    try:
        return ServerZone(key, shift)
    except ValueError:
        return None
