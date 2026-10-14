"""The broker's server clock: the wall clock of an IANA time zone plus a fixed shift."""

from __future__ import annotations

import math
import numbers
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import cache
from typing import Literal
from zoneinfo import ZoneInfo, available_timezones

from brokerclock._errors import AmbiguousTimeError, NonexistentTimeError

AmbiguousPolicy = Literal["raise", "earlier", "later"]

# MetaTrader epoch numbers count seconds from this moment on the server's own wall clock, so it has no time zone.
_EPOCH = datetime(1970, 1, 1)  # noqa: DTZ001
_SECOND = timedelta(seconds=1)
_HOUR = timedelta(hours=1)
_QUARTER_HOUR = timedelta(minutes=15)
_MIN_OFFSET = timedelta(hours=-12)
_MAX_OFFSET = timedelta(hours=14)
_MAX_SHIFT_HOURS = 26
_AMBIGUOUS_POLICIES = ("raise", "earlier", "later")

# Daylight-saving rule -> (IANA zone that follows it, that zone's standard UTC offset).
_RULES: dict[str, tuple[str, timedelta]] = {
    "US": ("America/New_York", timedelta(hours=-5)),
    "EU": ("Europe/London", timedelta(0)),
    "AU": ("Australia/Sydney", timedelta(hours=10)),
    "fixed": ("UTC", timedelta(0)),
}


@dataclass(frozen=True)
class ServerZone:
    """A broker server clock: the wall clock of ``zone`` (an IANA key) plus ``shift``.

    ``shift`` is a multiple of 15 minutes, at most 26 hours either way, and the resulting UTC offset must lie
    between -12 h and +14 h. The constructors cover the usual broker clocks:

    >>> str(ServerZone.us())
    'GMT+2/+3 (US daylight saving)'
    >>> str(ServerZone.eu(1))
    'GMT+1/+2 (EU daylight saving)'
    >>> str(ServerZone.fixed(5.75))
    'GMT+5:45 (fixed)'
    >>> str(ServerZone("Europe/Athens"))
    'Europe/Athens'
    """

    zone: str
    shift: timedelta = timedelta(0)

    def __post_init__(self) -> None:
        if self.zone not in _iana_keys():
            raise ValueError(f"unknown IANA time zone key: {self.zone!r}")
        if not isinstance(self.shift, timedelta):
            raise TypeError(f"shift must be a datetime.timedelta, got {type(self.shift).__name__}")
        if self.shift % _QUARTER_HOUR or abs(self.shift) > _MAX_SHIFT_HOURS * _HOUR:
            raise ValueError(f"shift must be a multiple of 15 minutes within 26 hours, got {self.shift!r}")
        year = datetime.now(timezone.utc).year
        for month in (1, 7):
            offset = self.offset_at(datetime(year, month, 1, tzinfo=timezone.utc))
            if not _MIN_OFFSET <= offset <= _MAX_OFFSET:
                raise ValueError(
                    f"{self.zone} shifted by {self.shift!r} gives GMT{_format_offset(offset)}, outside -12 h to +14 h"
                )

    @classmethod
    def us(cls, base_hours: float = 2) -> ServerZone:
        """New York wall clock plus ``base_hours + 5`` h: GMT+base in winter, GMT+base+1 in summer, US dates.

        The default, GMT+2/+3, is the most common broker clock: its midnight is 17:00 in New York, when the
        forex day rolls over.
        """
        return cls._from_rule("US", base_hours)

    @classmethod
    def eu(cls, base_hours: float = 2) -> ServerZone:
        """London wall clock plus ``base_hours``: GMT+base in winter, GMT+base+1 in summer, EU dates.

        ``eu()`` is GMT+2/+3 and ``eu(1)`` is GMT+1/+2, both switching on the last Sundays of March and October.
        """
        return cls._from_rule("EU", base_hours)

    @classmethod
    def au(cls, base_hours: float = 2) -> ServerZone:
        """Sydney wall clock plus ``base_hours - 10`` h: GMT+base+1 from October to April, GMT+base otherwise."""
        return cls._from_rule("AU", base_hours)

    @classmethod
    def fixed(cls, hours: float) -> ServerZone:
        """A clock that never switches: UTC plus ``hours`` (0.25 steps, so ``fixed(5.75)`` is GMT+5:45)."""
        return cls._from_rule("fixed", hours)

    @classmethod
    def _from_rule(cls, rule: str, base_hours: float) -> ServerZone:
        key, standard = _RULES[rule]
        return cls(key, _hours(base_hours) - standard)

    def __str__(self) -> str:
        for rule, (key, standard) in _RULES.items():
            if self.zone == key:
                base = standard + self.shift
                if rule == "fixed":
                    return f"GMT{_format_offset(base)} (fixed)"
                return f"GMT{_format_offset(base)}/{_format_offset(base + _HOUR)} ({rule} daylight saving)"
        return f"{self.zone} {_format_offset(self.shift)}" if self.shift else self.zone

    @property
    def _tz(self) -> ZoneInfo:
        return ZoneInfo(self.zone)

    def offset_at(self, instant: datetime) -> timedelta:
        """Return the server's UTC offset at an aware ``instant``.

        >>> ServerZone.us().offset_at(datetime(2025, 3, 20, tzinfo=timezone.utc))  # US summer time, EU not yet
        datetime.timedelta(seconds=10800)
        >>> ServerZone.eu().offset_at(datetime(2025, 3, 20, tzinfo=timezone.utc))
        datetime.timedelta(seconds=7200)
        """
        _require_aware(instant, "instant")
        return instant.astimezone(self._tz).utcoffset() + self.shift

    def to_utc(self, server_time: float | datetime, *, ambiguous: AmbiguousPolicy = "raise") -> datetime:
        """Convert a server time to an aware UTC datetime.

        ``server_time`` is epoch seconds as MetaTrader returns them (``time``, or ``time_msc / 1000``), or a naive
        datetime of server wall time. An aware datetime raises ``TypeError``, because it is unclear whether it was
        already converted.

        A wall time in the hour repeated when daylight saving ends is resolved by ``ambiguous``: ``"raise"`` raises
        ``AmbiguousTimeError``, ``"earlier"`` takes the first occurrence and ``"later"`` the second. A wall time in
        the hour skipped when daylight saving starts raises ``NonexistentTimeError``.

        >>> zone = ServerZone.us()
        >>> zone.to_utc(1735693200)  # 2025-01-01 01:00 on the server clock
        datetime.datetime(2024, 12, 31, 23, 0, tzinfo=datetime.timezone.utc)
        >>> zone.to_utc(datetime(2025, 7, 1, 12, 0))
        datetime.datetime(2025, 7, 1, 9, 0, tzinfo=datetime.timezone.utc)
        """
        if ambiguous not in _AMBIGUOUS_POLICIES:
            raise ValueError(f"ambiguous must be one of {_AMBIGUOUS_POLICIES}, got {ambiguous!r}")
        wall = _server_wall(server_time)
        local = wall - self.shift
        tz = self._tz
        earlier = local.replace(tzinfo=tz, fold=0).astimezone(timezone.utc)
        later = local.replace(tzinfo=tz, fold=1).astimezone(timezone.utc)
        if earlier == later:
            return earlier
        if earlier.astimezone(tz).replace(tzinfo=None) != local:
            raise NonexistentTimeError(f"server time {wall} falls in the hour that {self} skips")
        if ambiguous == "raise":
            raise AmbiguousTimeError(f"server time {wall} occurs twice in {self}; pass ambiguous='earlier' or 'later'")
        return earlier if ambiguous == "earlier" else later

    def to_server(self, instant: datetime) -> datetime:
        """Return the naive server wall time at an aware ``instant``.

        >>> ServerZone.us().to_server(datetime(2025, 7, 1, 9, 0, tzinfo=timezone.utc))
        datetime.datetime(2025, 7, 1, 12, 0)
        """
        offset = self.offset_at(instant)
        return instant.astimezone(timezone.utc).replace(tzinfo=None) + offset

    def server_timestamp(self, instant: datetime) -> int:
        """Return the server-wall epoch seconds of an aware ``instant``, flooring any fraction of a second.

        Pass these numbers to ``copy_rates_range``, ``copy_ticks_range`` and ``history_deals_get``, and as the
        ``expiration`` of an order: MetaTrader reads all of them as server wall time. An int also avoids the
        terminal reading a naive datetime in the PC's local zone.

        >>> ServerZone.us().server_timestamp(datetime(2025, 7, 1, 9, 0, 0, 999999, tzinfo=timezone.utc))
        1751371200
        """
        return (self.to_server(instant) - _EPOCH) // _SECOND


@cache
def _iana_keys() -> frozenset[str]:
    return frozenset(available_timezones())


def _hours(value: float) -> timedelta:
    """Turn a number of hours in 0.25 steps into a timedelta."""
    valid = (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and abs(value) <= _MAX_SHIFT_HOURS
        and float(value * 4).is_integer()
    )
    if not valid:
        raise ValueError(f"hours must be an int or float in 0.25 steps, got {value!r}")
    return timedelta(minutes=round(value * 60))


def _require_aware(value: object, name: str) -> None:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise TypeError(f"{name} must be an aware datetime (with tzinfo), got {value!r}")


def _server_wall(server_time: float | datetime) -> datetime:
    """Turn epoch seconds or a naive datetime of server wall time into a naive datetime."""
    if isinstance(server_time, datetime):
        if server_time.utcoffset() is not None:
            raise TypeError("server_time must be naive server wall time or epoch seconds, not an aware datetime")
        return server_time
    if isinstance(server_time, bool) or not isinstance(server_time, numbers.Real):
        raise TypeError(f"server_time must be epoch seconds or a naive datetime, got {server_time!r}")
    if isinstance(server_time, numbers.Integral):
        return _EPOCH + timedelta(seconds=int(server_time))
    seconds = float(server_time)
    if not math.isfinite(seconds):
        raise ValueError(f"server_time must be finite, got {server_time!r}")
    return _EPOCH + timedelta(seconds=seconds)


def _nearest_quarter(offset: timedelta) -> timedelta:
    """Round to the nearest 15 minutes, halves up."""
    return _QUARTER_HOUR * math.floor(offset / _QUARTER_HOUR + 0.5)


def _format_offset(offset: timedelta) -> str:
    """Format an offset as +2, -3:30 or +5:45."""
    minutes = offset // timedelta(minutes=1)
    sign = "-" if minutes < 0 else "+"
    hours, minutes = divmod(abs(minutes), 60)
    return f"{sign}{hours}:{minutes:02d}" if minutes else f"{sign}{hours}"
