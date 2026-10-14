"""MetaTrader broker server time to UTC and back, with daylight saving done right.

>>> from datetime import datetime, timezone
>>> from brokerclock import ServerZone
>>> zone = ServerZone.us()  # GMT+2/+3 following US daylight saving, the most common broker clock
>>> zone.to_utc(1751371200)  # a bar time as MetaTrader returns it: 2025-07-01 12:00 on the server clock
datetime.datetime(2025, 7, 1, 9, 0, tzinfo=datetime.timezone.utc)
>>> zone.server_timestamp(datetime(2025, 7, 1, 9, 0, tzinfo=timezone.utc))
1751371200
"""

from brokerclock._detect import Detection, WeekClose, detect
from brokerclock._errors import (
    AmbiguousTimeError,
    BrokerClockError,
    NonexistentTimeError,
    NotEnoughData,
    OffsetOutOfRange,
    StaleTickError,
    ZoneMismatchError,
)
from brokerclock._live import check, measure_offset
from brokerclock._zone import ServerZone

__version__ = "0.1.0"

__all__ = [
    "AmbiguousTimeError",
    "BrokerClockError",
    "Detection",
    "NonexistentTimeError",
    "NotEnoughData",
    "OffsetOutOfRange",
    "ServerZone",
    "StaleTickError",
    "WeekClose",
    "ZoneMismatchError",
    "__version__",
    "check",
    "detect",
    "measure_offset",
]
