"""Vectorized conversions for whole pandas columns. Needs pandas: ``pip install "brokerclock[pandas]"``.

>>> import brokerclock.pandas as bcpd
>>> from brokerclock import ServerZone
>>> utc = bcpd.to_utc([1751371200, 1751374800], ServerZone.us())  # 12:00 and 13:00 on the server clock
>>> [str(t) for t in utc]
['2025-07-01 09:00:00+00:00', '2025-07-01 10:00:00+00:00']
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from brokerclock._zone import _EPOCH, ServerZone

try:
    import pandas as pd
except ImportError as exc:
    raise ImportError('brokerclock.pandas needs pandas: pip install "brokerclock[pandas]"') from exc

if TYPE_CHECKING:
    import numpy as np
    import numpy.typing as npt

    Times = pd.Series | pd.DatetimeIndex


def to_utc(
    values: npt.ArrayLike,
    zone: ServerZone,
    *,
    unit: str = "s",
    ambiguous: str | npt.ArrayLike = "infer",
    nonexistent: str = "raise",
) -> Times:
    """Convert server epoch numbers to aware UTC times.

    ``values`` is a Series, Index or array of server epoch numbers in ``unit`` (``"s"`` for ``time``, ``"ms"`` for
    ``time_msc``). A Series comes back as a Series with the same index; anything else as a DatetimeIndex.

    ``ambiguous`` and ``nonexistent`` are passed to pandas' ``tz_localize``. The default ``"infer"`` resolves the
    repeated hour when the values are in time order, as bars and ticks are, and raises when it cannot (for example
    a single value, or one merged H1 bar). A value in a skipped hour raises by default. Both errors are pandas'
    own: ``ValueError`` in pandas 3, pytz's ``AmbiguousTimeError`` or ``NonExistentTimeError`` in pandas 2.
    """
    times = pd.to_datetime(values, unit=unit) - zone.shift
    local = _datetimes(times).tz_localize(zone.zone, ambiguous=ambiguous, nonexistent=nonexistent)
    return _datetimes(local).tz_convert("UTC")


def frame_to_utc(
    frame: pd.DataFrame | np.ndarray, zone: ServerZone, column: str = "time", **kwargs: Any
) -> pd.DataFrame:
    """Return a copy of ``frame`` with ``column`` converted by :func:`to_utc`.

    ``frame`` is a DataFrame or the structured array that ``copy_rates_*`` and ``copy_ticks_*`` return.
    ``kwargs`` go to :func:`to_utc`; for tick arrays pass ``column="time_msc", unit="ms"``.
    """
    out = frame.copy() if isinstance(frame, pd.DataFrame) else pd.DataFrame(frame)
    out[column] = to_utc(out[column], zone, **kwargs)
    return out


def to_server_timestamps(index_or_series: Times, zone: ServerZone) -> npt.NDArray[np.int64]:
    """Convert aware times to server epoch seconds (int64, fractions of a second floored).

    The inverse of :func:`to_utc`, for building query bounds or comparing with MetaTrader data.
    """
    times = pd.DatetimeIndex(index_or_series)
    if times.tz is None:
        raise TypeError("to_server_timestamps needs aware times (for example UTC), got naive ones")
    wall = times.tz_convert(zone.zone).tz_localize(None) + zone.shift
    return ((wall - pd.Timestamp(_EPOCH)) // pd.Timedelta(seconds=1)).to_numpy(dtype="int64")


def _datetimes(times: Times) -> Any:
    """The object that carries the datetime methods: ``.dt`` for a Series, the index itself otherwise."""
    return times.dt if isinstance(times, pd.Series) else times
