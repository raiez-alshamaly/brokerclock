"""Measure or confirm the server clock from live tick times and the PC clock."""

from __future__ import annotations

import numbers
import time
from collections.abc import Iterable
from datetime import datetime, timedelta

from brokerclock._errors import NonexistentTimeError, OffsetOutOfRange, StaleTickError, ZoneMismatchError
from brokerclock._zone import _MAX_OFFSET, _MIN_OFFSET, _QUARTER_HOUR, ServerZone, _nearest_quarter, _require_aware


def measure_offset(
    server_times: Iterable[float], now: float | datetime | None = None, *, tolerance: float = 60
) -> timedelta:
    """Measure the server's current UTC offset from the freshest tick time.

    ``server_times`` are tick times in server epoch seconds (``tick.time``, or ``tick.time_msc / 1000``); the
    largest is used. ``now`` is the current UTC time as epoch seconds or an aware datetime, and defaults to the PC
    clock. The offset is ``freshest - now`` rounded to 15 minutes.

    Raises ``OffsetOutOfRange`` when the offset is outside -12 h to +14 h, and ``StaleTickError`` when the
    unrounded difference is more than ``tolerance`` seconds from the offset: the tick is not fresh, or the PC
    clock is off by more than ``tolerance``.

    A tick that is stale by close to a multiple of 15 minutes passes this test with a wrong offset, so measure
    only while the symbols trade, or prefer :func:`check` against a zone found by :func:`detect`.

    >>> measure_offset([1751382000], now=1751371200.6)  # a tick from 0.6 s ago, 3 hours ahead of UTC
    datetime.timedelta(seconds=10800)
    """
    if isinstance(tolerance, bool) or not 0 <= tolerance < _QUARTER_HOUR.total_seconds() / 2:
        raise ValueError(f"tolerance must be at least 0 and under 450 seconds, got {tolerance!r}")
    raw = timedelta(seconds=float(_freshest(server_times)) - _now_seconds(now))
    offset = _nearest_quarter(raw)
    if not _MIN_OFFSET <= offset <= _MAX_OFFSET:
        raise OffsetOutOfRange(
            f"the freshest tick is {raw.total_seconds() / 3600:+.2f} h from the PC clock, outside -12 h to +14 h: "
            "the tick is old (market closed?) or the PC clock is wrong"
        )
    residual = (raw - offset).total_seconds()
    if abs(residual) > tolerance:
        raise StaleTickError(
            f"the freshest tick is {residual:+.1f} s off a whole quarter hour from the PC clock: "
            f"either the tick is not fresh or the PC clock is off by more than {tolerance} s"
        )
    return offset


def check(
    zone: ServerZone,
    server_times: Iterable[float],
    now: float | datetime | None = None,
    *,
    tolerance: float = 60,
    max_age: float = 300,
) -> bool:
    """Check live tick times against ``zone``.

    The age of the freshest tick is ``now`` minus its UTC time under ``zone``. ``server_times`` and ``now`` are as
    in :func:`measure_offset`. Three outcomes:

    * ``ZoneMismatchError`` when the tick is more than ``tolerance`` seconds in the future: the zone is behind
      the real server clock, or the PC clock is behind.
    * ``True`` when the tick is at most ``max_age`` seconds old: a fresh tick agrees with the zone.
    * ``False`` when the tick is older: nothing can be confirmed, because the market is closed or quiet, or the
      zone is ahead of the real one.

    A tick in an hour the clock repeats is checked against both occurrences.

    >>> from brokerclock import ServerZone
    >>> check(ServerZone.us(), [1751382000], now=1751371200.6)  # 2025-07-01 12:00 server is 09:00 UTC
    True
    """
    if tolerance < 0 or max_age < 0:
        raise ValueError(f"tolerance and max_age must be at least 0, got {tolerance!r} and {max_age!r}")
    freshest = _freshest(server_times)
    try:
        earlier = zone.to_utc(freshest, ambiguous="earlier")
    except NonexistentTimeError as exc:
        raise ZoneMismatchError(f"tick time {freshest} falls in an hour that {zone} skips") from exc
    later = zone.to_utc(freshest, ambiguous="later")
    current = _now_seconds(now)
    if current - earlier.timestamp() < -tolerance:
        raise ZoneMismatchError(
            f"under {zone} the freshest tick is from {earlier:%Y-%m-%d %H:%M:%S} UTC, in the future: "
            "the zone is behind the real server clock or the PC clock is behind"
        )
    return any(-tolerance <= current - tick.timestamp() <= max_age for tick in (earlier, later))


def _freshest(server_times: Iterable[float]) -> float:
    freshest = max(server_times, default=None)
    if freshest is None:
        raise ValueError("server_times is empty")
    return freshest


def _now_seconds(now: float | datetime | None) -> float:
    if now is None:
        return time.time()
    if isinstance(now, datetime):
        _require_aware(now, "now")
        return now.timestamp()
    if isinstance(now, bool) or not isinstance(now, numbers.Real):
        raise TypeError(f"now must be epoch seconds or an aware datetime, got {now!r}")
    return float(now)
