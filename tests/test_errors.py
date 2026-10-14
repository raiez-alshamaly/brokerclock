import re
from pathlib import Path

import pytest

import brokerclock
from brokerclock import (
    AmbiguousTimeError,
    BrokerClockError,
    NonexistentTimeError,
    NotEnoughData,
    OffsetOutOfRange,
    StaleTickError,
    ZoneMismatchError,
)

ERRORS = [AmbiguousTimeError, NonexistentTimeError, NotEnoughData, StaleTickError, OffsetOutOfRange, ZoneMismatchError]


@pytest.mark.parametrize("error", ERRORS)
def test_every_error_is_a_broker_clock_error_and_a_value_error(error):
    assert issubclass(error, BrokerClockError)
    assert issubclass(error, ValueError)
    assert error.__doc__


def test_base_error_is_a_value_error():
    assert issubclass(BrokerClockError, ValueError)


def test_public_names_are_exported():
    assert sorted(brokerclock.__all__) == sorted(
        [error.__name__ for error in [BrokerClockError, *ERRORS]]
        + ["Detection", "ServerZone", "WeekClose", "__version__", "check", "detect", "measure_offset"]
    )
    assert all(hasattr(brokerclock, name) for name in brokerclock.__all__)


def test_version_is_a_release_number():
    assert re.fullmatch(r"\d+\.\d+\.\d+", brokerclock.__version__)
    changelog = (Path(__file__).resolve().parent.parent / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## [{brokerclock.__version__}]" in changelog
