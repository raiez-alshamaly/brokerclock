from datetime import date, datetime, timedelta, timezone

import pytest

import oracle
from brokerclock import OffsetOutOfRange, ServerZone, StaleTickError, ZoneMismatchError, check, measure_offset

UTC = timezone.utc
NOW = 1751371200.0  # 2025-07-01 09:00 UTC: GMT+3 on a US-rule broker
THREE_HOURS = 3 * oracle.HOUR


# measure_offset


def test_fresh_tick():
    assert measure_offset([NOW + THREE_HOURS - 0.8], now=NOW) == timedelta(hours=3)


def test_freshest_of_many_ticks_counts():
    ticks = [NOW + THREE_HOURS - 3000, NOW + THREE_HOURS - 0.3, NOW + THREE_HOURS - 900]
    assert measure_offset(ticks, now=NOW) == timedelta(hours=3)


def test_time_msc_in_seconds():
    time_msc = int((NOW + THREE_HOURS - 0.25) * 1000)
    assert measure_offset([time_msc / 1000], now=NOW) == timedelta(hours=3)


def test_now_as_aware_datetime():
    now = datetime.fromtimestamp(NOW, UTC)
    assert measure_offset([NOW + THREE_HOURS], now=now) == timedelta(hours=3)


def test_now_defaults_to_the_pc_clock(monkeypatch):
    monkeypatch.setattr("brokerclock._live.time.time", lambda: NOW)
    assert measure_offset([NOW + THREE_HOURS - 1]) == timedelta(hours=3)


@pytest.mark.parametrize("now", [datetime(2025, 7, 1, 9), "1751371200", True])
def test_now_must_be_epoch_seconds_or_aware(now):
    with pytest.raises(TypeError):
        measure_offset([NOW + THREE_HOURS], now=now)


@pytest.mark.parametrize("lag", [200, -200, 61])
def test_stale_or_future_tick_raises(lag):
    with pytest.raises(StaleTickError, match="not fresh or the PC clock is off"):
        measure_offset([NOW + THREE_HOURS - lag], now=NOW)


def test_tolerance_is_adjustable():
    assert measure_offset([NOW + THREE_HOURS - 100], now=NOW, tolerance=120) == timedelta(hours=3)
    with pytest.raises(StaleTickError):
        measure_offset([NOW + THREE_HOURS - 1], now=NOW, tolerance=0.5)


def test_a_tick_stale_by_a_whole_hour_cannot_be_told_apart():
    # Documented limit: this tick is an hour old, and the result is one hour short.
    assert measure_offset([NOW + THREE_HOURS - 3600], now=NOW) == timedelta(hours=2)


@pytest.mark.parametrize("tick", [NOW + 15 * 3600, NOW - 13 * 3600, NOW + THREE_HOURS - 3 * oracle.DAY])
def test_offset_out_of_range(tick):
    with pytest.raises(OffsetOutOfRange, match="outside -12 h to \\+14 h"):
        measure_offset([tick], now=NOW)


@pytest.mark.parametrize(
    ("tick", "expected"),
    [
        (NOW + 5 * 3600 + 45 * 60 - 1, timedelta(hours=5, minutes=45)),
        (NOW - 3.5 * 3600 - 2, timedelta(hours=-3, minutes=-30)),
        (NOW + 14 * 3600, timedelta(hours=14)),
        (NOW - 12 * 3600, timedelta(hours=-12)),
    ],
)
def test_quarter_hour_and_extreme_offsets(tick, expected):
    assert measure_offset([tick], now=NOW) == expected


def test_empty_input_is_rejected():
    with pytest.raises(ValueError, match="empty"):
        measure_offset([], now=NOW)


@pytest.mark.parametrize("tolerance", [-1, 450, 1000, True])
def test_tolerance_must_be_below_half_a_quarter_hour(tolerance):
    with pytest.raises(ValueError, match="tolerance"):
        measure_offset([NOW + THREE_HOURS], now=NOW, tolerance=tolerance)


# check


def test_check_true_for_a_fresh_tick_that_agrees():
    assert check(ServerZone.us(), [NOW + THREE_HOURS - 2], now=NOW) is True
    assert check(ServerZone.us(), [NOW + THREE_HOURS + 30], now=NOW) is True  # within the tolerance


def test_check_false_for_an_old_tick():
    assert check(ServerZone.us(), [NOW + THREE_HOURS - 3600], now=NOW) is False


def test_check_false_when_the_zone_is_ahead():
    assert check(ServerZone.us(3), [NOW + THREE_HOURS - 2], now=NOW) is False


def test_check_raises_when_the_zone_is_behind():
    # GMT+2 all year is an hour behind this server in summer, so its tick looks an hour in the future.
    with pytest.raises(ZoneMismatchError, match="in the future"):
        check(ServerZone.fixed(2), [NOW + THREE_HOURS - 2], now=NOW)


def test_check_raises_when_the_pc_clock_is_behind():
    with pytest.raises(ZoneMismatchError):
        check(ServerZone.us(), [NOW + THREE_HOURS], now=NOW - 120)


def test_check_limits_are_adjustable():
    assert check(ServerZone.us(), [NOW + THREE_HOURS - 3600], now=NOW, max_age=3600) is True
    assert check(ServerZone.us(), [NOW + THREE_HOURS + 100], now=NOW, tolerance=120) is True


def test_check_raises_for_a_tick_in_a_skipped_hour():
    tick = oracle.epoch(date(2025, 3, 9), 9, 30)  # 02:30 New York does not exist that day
    with pytest.raises(ZoneMismatchError, match="skips"):
        check(ServerZone.us(), [tick], now=tick)


@pytest.mark.parametrize(
    "now", [datetime(2025, 10, 26, 0, 30, 1, tzinfo=UTC), datetime(2025, 10, 26, 1, 30, 1, tzinfo=UTC)]
)
def test_check_accepts_a_fresh_tick_in_either_occurrence_of_a_repeated_hour(now):
    # A 24/7 symbol on an EU-rule GMT+2/+3 server: 03:30 server time happens at 00:30 and again at 01:30 UTC.
    tick = oracle.epoch(date(2025, 10, 26), 3, 30)
    assert check(ServerZone.eu(), [tick], now=now) is True


def test_check_rejects_empty_input_and_negative_limits():
    with pytest.raises(ValueError, match="empty"):
        check(ServerZone.us(), [], now=NOW)
    with pytest.raises(ValueError, match="at least 0"):
        check(ServerZone.us(), [NOW], now=NOW, max_age=-1)
    with pytest.raises(ValueError, match="at least 0"):
        check(ServerZone.us(), [NOW], now=NOW, tolerance=-1)
