"""Exceptions raised by brokerclock. All of them are ``ValueError`` subclasses."""


class BrokerClockError(ValueError):
    """Base class of every brokerclock error."""


class AmbiguousTimeError(BrokerClockError):
    """A server wall time occurs twice (the hour repeated when daylight saving ends)."""


class NonexistentTimeError(BrokerClockError):
    """A server wall time never occurs (the hour skipped when daylight saving starts)."""


class NotEnoughData(BrokerClockError):
    """The bar history holds fewer than two weekly closes that can be matched to a Friday."""


class StaleTickError(BrokerClockError):
    """A tick time is not a whole quarter hour away from the PC clock: the tick is old or the PC clock is off."""


class OffsetOutOfRange(BrokerClockError):
    """A measured offset lies outside the possible UTC offsets, -12 h to +14 h."""


class ZoneMismatchError(BrokerClockError):
    """A live tick contradicts the server zone: the zone is wrong or the PC clock is behind."""
