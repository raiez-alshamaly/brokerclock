import dataclasses
import math
from datetime import date, datetime, timedelta, timezone

import pytest
from hypothesis import given
from hypothesis import strategies as st

import oracle
from brokerclock import AmbiguousTimeError, NonexistentTimeError, ServerZone

UTC = timezone.utc
HOUR = timedelta(hours=1)

# (zone, oracle rule, base hours)
CONVENTIONS = [
    (ServerZone.us(), "US", 2),
    (ServerZone.us(3), "US", 3),
    (ServerZone.eu(), "EU", 2),
    (ServerZone.eu(1), "EU", 1),
    (ServerZone.au(), "AU", 2),
    (ServerZone.fixed(0), "fixed", 0),
    (ServerZone.fixed(2), "fixed", 2),
    (ServerZone.fixed(3), "fixed", 3),
    (ServerZone.fixed(5.75), "fixed", 5.75),
    (ServerZone.fixed(-3.5), "fixed", -3.5),
]
CONVENTION_IDS = [str(zone) for zone, _, _ in CONVENTIONS]


def utc(t: int) -> datetime:
    return datetime.fromtimestamp(t, UTC)


@pytest.mark.parametrize(
    ("zone", "key", "shift_hours", "text"),
    [
        (ServerZone.us(), "America/New_York", 7, "GMT+2/+3 (US daylight saving)"),
        (ServerZone.us(3), "America/New_York", 8, "GMT+3/+4 (US daylight saving)"),
        (ServerZone.us(2.5), "America/New_York", 7.5, "GMT+2:30/+3:30 (US daylight saving)"),
        (ServerZone.eu(), "Europe/London", 2, "GMT+2/+3 (EU daylight saving)"),
        (ServerZone.eu(1), "Europe/London", 1, "GMT+1/+2 (EU daylight saving)"),
        (ServerZone.au(), "Australia/Sydney", -8, "GMT+2/+3 (AU daylight saving)"),
        (ServerZone.fixed(0), "UTC", 0, "GMT+0 (fixed)"),
        (ServerZone.fixed(2), "UTC", 2, "GMT+2 (fixed)"),
        (ServerZone.fixed(5.75), "UTC", 5.75, "GMT+5:45 (fixed)"),
        (ServerZone.fixed(-3.5), "UTC", -3.5, "GMT-3:30 (fixed)"),
        (ServerZone("Europe/Athens"), "Europe/Athens", 0, "Europe/Athens"),
        (ServerZone("Europe/Berlin", timedelta(hours=1)), "Europe/Berlin", 1, "Europe/Berlin +1"),
        (ServerZone("Asia/Tokyo", timedelta(minutes=-90)), "Asia/Tokyo", -1.5, "Asia/Tokyo -1:30"),
    ],
    ids=lambda value: str(value) if isinstance(value, ServerZone) else None,
)
def test_constructors(zone, key, shift_hours, text):
    assert zone.zone == key
    assert zone.shift == timedelta(hours=shift_hours)
    assert str(zone) == text


def test_zones_are_frozen_values():
    assert ServerZone.us() == ServerZone("America/New_York", timedelta(hours=7)) == ServerZone.us(2.0)
    assert len({ServerZone.us(), ServerZone.us(2.0), ServerZone.eu()}) == 2
    with pytest.raises(dataclasses.FrozenInstanceError):
        ServerZone.us().shift = HOUR  # type: ignore[misc]


def _instants() -> list[int]:
    """Every 6 hours across 2024-2028, plus a second before, at and after every switch."""
    start, end = oracle.epoch(date(2024, 1, 1)), oracle.epoch(date(2029, 1, 1))
    instants = list(range(start, end, 6 * 3600))
    for year in range(2024, 2029):
        for rule in ("US", "EU", "AU"):
            instants.extend(edge + delta for edge in oracle.switches(rule, year) for delta in (-1, 0, 1))
    return instants


INSTANTS = _instants()


@pytest.mark.parametrize(("zone", "rule", "base"), CONVENTIONS, ids=CONVENTION_IDS)
def test_offsets_match_the_published_rules_2024_to_2028(zone, rule, base):
    for t in INSTANTS:
        assert zone.offset_at(utc(t)) == timedelta(seconds=oracle.offset(rule, base, t)), utc(t)


@pytest.mark.parametrize(
    ("when", "us_hours", "eu_hours"),
    [
        (datetime(2025, 3, 7, 12, tzinfo=UTC), 2, 2),  # both in winter time
        (datetime(2025, 3, 12, 12, tzinfo=UTC), 3, 2),  # US switched on 9 March, the EU waits until 30 March
        (datetime(2025, 3, 31, 12, tzinfo=UTC), 3, 3),
        (datetime(2025, 10, 29, 12, tzinfo=UTC), 3, 2),  # the EU switched back on 26 October, US on 2 November
        (datetime(2025, 11, 3, 12, tzinfo=UTC), 2, 2),
        (datetime(2026, 3, 10, 12, tzinfo=UTC), 3, 2),  # US 8 March, EU 29 March
        (datetime(2026, 10, 28, 12, tzinfo=UTC), 3, 2),  # EU 25 October, US 1 November
    ],
)
def test_us_and_eu_differ_in_the_mismatch_weeks(when, us_hours, eu_hours):
    assert ServerZone.us().offset_at(when) == us_hours * HOUR
    assert ServerZone.eu().offset_at(when) == eu_hours * HOUR


@pytest.mark.parametrize("hours", [0, 2, 3, 5.75, -3.5, -12, 14])
def test_fixed_offsets_never_change(hours):
    zone = ServerZone.fixed(hours)
    assert {zone.offset_at(utc(t)) for t in INSTANTS} == {timedelta(hours=hours)}


def test_offset_at_accepts_any_aware_timezone():
    tokyo = timezone(timedelta(hours=9))
    assert ServerZone.us().offset_at(datetime(2025, 7, 1, 18, tzinfo=tokyo)) == 3 * HOUR


@pytest.mark.parametrize("instant", [datetime(2025, 7, 1, 12), "2025-07-01", 1751371200, date(2025, 7, 1)])
def test_offset_at_needs_an_aware_datetime(instant):
    with pytest.raises(TypeError, match="aware"):
        ServerZone.us().offset_at(instant)


@pytest.mark.parametrize("key", ["Mars/Olympus", "america/new_york", "America/New_York ", "", "../x", "America"])
def test_unknown_zone_keys_are_rejected(key):
    with pytest.raises(ValueError, match="IANA"):
        ServerZone(key)


@pytest.mark.parametrize(
    "shift",
    [timedelta(minutes=10), timedelta(seconds=30), timedelta(microseconds=1), timedelta(hours=27), -27 * HOUR],
)
def test_shift_must_be_whole_quarter_hours_within_26_hours(shift):
    with pytest.raises(ValueError, match="multiple of 15 minutes"):
        ServerZone("UTC", shift)


def test_shift_must_be_a_timedelta():
    with pytest.raises(TypeError, match="timedelta"):
        ServerZone("UTC", 2)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "build",
    [
        lambda: ServerZone.fixed(14.25),
        lambda: ServerZone.fixed(-12.25),
        lambda: ServerZone.us(13.5),  # +14:30 in summer
        lambda: ServerZone.eu(14),
        lambda: ServerZone.au(-12.25),
        lambda: ServerZone("Pacific/Kiritimati", HOUR),  # +14 there already
    ],
)
def test_offsets_outside_minus_12_to_plus_14_are_rejected(build):
    with pytest.raises(ValueError, match="outside -12 h to \\+14 h"):
        build()


def test_offsets_at_the_limits_are_accepted():
    assert ServerZone.us(13).offset_at(datetime(2025, 7, 1, tzinfo=UTC)) == 14 * HOUR
    assert ServerZone.au(-12).offset_at(datetime(2025, 7, 1, tzinfo=UTC)) == -12 * HOUR


@pytest.mark.parametrize("hours", [0.1, 2.2, 1 / 3, "2", None, True, math.nan, math.inf, 27, -27, 1e300])
@pytest.mark.parametrize("constructor", [ServerZone.us, ServerZone.eu, ServerZone.au, ServerZone.fixed])
def test_hours_must_be_quarter_steps(constructor, hours):
    with pytest.raises(ValueError, match=r"0\.25 steps"):
        constructor(hours)


# Round trips


@pytest.mark.parametrize(("zone", "rule", "base"), CONVENTIONS, ids=CONVENTION_IDS)
def test_round_trip_every_hour_of_2025_and_2026(zone, rule, base):
    for t in range(oracle.epoch(date(2025, 1, 1)), oracle.epoch(date(2027, 1, 1)), 3600):
        instant = utc(t)
        server = zone.server_timestamp(instant)
        assert server == t + oracle.offset(rule, base, t), instant
        earlier = zone.to_utc(server, ambiguous="earlier")
        later = zone.to_utc(server, ambiguous="later")
        assert instant in (earlier, later)
        assert later - earlier in (timedelta(0), HOUR)


ZONES = st.sampled_from([zone for zone, _, _ in CONVENTIONS] + [ServerZone("Asia/Kathmandu"), ServerZone.us(-3)])


@given(
    instant=st.datetimes(min_value=datetime(1990, 1, 1), max_value=datetime(2060, 1, 1), timezones=st.just(UTC)),
    zone=ZONES,
)
def test_round_trip_any_instant(instant, zone):
    server = zone.server_timestamp(instant)
    wall = zone.to_server(instant)
    assert wall.tzinfo is None
    assert server == (wall - datetime(1970, 1, 1)) // timedelta(seconds=1)
    whole_second = instant.replace(microsecond=0)
    assert whole_second in (zone.to_utc(server, ambiguous="earlier"), zone.to_utc(server, ambiguous="later"))
    assert instant in (zone.to_utc(wall, ambiguous="earlier"), zone.to_utc(wall, ambiguous="later"))


# The repeated and the skipped hour

REPEATED = [
    # zone, server wall time in the repeated hour, its first occurrence in UTC
    (ServerZone.us(), datetime(2025, 11, 2, 8, 30), datetime(2025, 11, 2, 5, 30, tzinfo=UTC)),  # New York 01:30
    (ServerZone.eu(), datetime(2025, 10, 26, 3, 30), datetime(2025, 10, 26, 0, 30, tzinfo=UTC)),  # London 01:30
    (ServerZone.au(), datetime(2025, 4, 5, 18, 30), datetime(2025, 4, 5, 15, 30, tzinfo=UTC)),  # Sydney 02:30
]
SKIPPED = [
    (ServerZone.us(), datetime(2025, 3, 9, 9, 30)),  # New York 02:30
    (ServerZone.us(), datetime(2025, 3, 9, 9, 0)),
    (ServerZone.eu(), datetime(2025, 3, 30, 3, 30)),  # London 01:30
    (ServerZone.au(), datetime(2025, 10, 4, 18, 30)),  # Sydney 02:30
]


def as_epoch(wall: datetime) -> int:
    return oracle.epoch(wall.date(), wall.hour, wall.minute)


@pytest.mark.parametrize(("zone", "wall", "first"), REPEATED)
@pytest.mark.parametrize("as_number", [False, True])
def test_repeated_hour(zone, wall, first, as_number):
    server_time = as_epoch(wall) if as_number else wall
    with pytest.raises(AmbiguousTimeError, match="occurs twice"):
        zone.to_utc(server_time)
    assert zone.to_utc(server_time, ambiguous="earlier") == first
    assert zone.to_utc(server_time, ambiguous="later") == first + HOUR


@pytest.mark.parametrize(("zone", "wall"), SKIPPED)
@pytest.mark.parametrize("ambiguous", ["raise", "earlier", "later"])
def test_skipped_hour_raises(zone, wall, ambiguous):
    with pytest.raises(NonexistentTimeError, match="skips"):
        zone.to_utc(wall, ambiguous=ambiguous)
    with pytest.raises(NonexistentTimeError):
        zone.to_utc(as_epoch(wall), ambiguous=ambiguous)


def test_hours_next_to_the_switch_are_unique():
    zone = ServerZone.us()
    assert zone.to_utc(datetime(2025, 3, 9, 8, 59, 59)) == datetime(2025, 3, 9, 6, 59, 59, tzinfo=UTC)
    assert zone.to_utc(datetime(2025, 3, 9, 10, 0)) == datetime(2025, 3, 9, 7, 0, tzinfo=UTC)
    assert zone.to_utc(datetime(2025, 11, 2, 9, 0)) == datetime(2025, 11, 2, 7, 0, tzinfo=UTC)


# Inputs


def test_to_utc_accepts_ints_floats_and_naive_datetimes():
    zone = ServerZone.us()
    expected = datetime(2025, 7, 1, 9, 0, tzinfo=UTC)
    assert zone.to_utc(1751371200) == expected
    assert zone.to_utc(1751371200.0) == expected
    assert zone.to_utc(datetime(2025, 7, 1, 12, 0)) == expected
    assert zone.to_utc(1751371200123 / 1000) == expected.replace(microsecond=123000)
    assert zone.to_utc(datetime(2025, 7, 1, 12, 0)).tzinfo is UTC


def test_to_utc_accepts_numpy_integers():
    np = pytest.importorskip("numpy")
    assert ServerZone.us().to_utc(np.int64(1751371200)) == datetime(2025, 7, 1, 9, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "server_time", [datetime(2025, 7, 1, 12, tzinfo=UTC), True, False, "1751371200", None, [1751371200]]
)
def test_to_utc_rejects_aware_bool_and_other_types(server_time):
    with pytest.raises(TypeError):
        ServerZone.us().to_utc(server_time)


@pytest.mark.parametrize("server_time", [math.nan, math.inf])
def test_to_utc_rejects_non_finite_numbers(server_time):
    with pytest.raises(ValueError, match="finite"):
        ServerZone.us().to_utc(server_time)


def test_to_utc_rejects_unknown_ambiguous_policy():
    with pytest.raises(ValueError, match="ambiguous"):
        ServerZone.us().to_utc(1751371200, ambiguous="first")  # type: ignore[arg-type]


def test_server_timestamp_floors_fractions():
    zone = ServerZone.fixed(0)
    assert zone.server_timestamp(datetime(2025, 7, 1, 9, 0, 0, 999999, tzinfo=UTC)) == 1751360400
    assert zone.server_timestamp(datetime(1969, 12, 31, 23, 59, 59, 500000, tzinfo=UTC)) == -1


def test_server_timestamp_for_query_bounds_and_expiration():
    zone = ServerZone.us()
    # 2025-03-12 is in a mismatch week: the server runs at GMT+3 while an EU-rule clock would say GMT+2.
    assert zone.server_timestamp(datetime(2025, 3, 12, 12, tzinfo=UTC)) == oracle.epoch(date(2025, 3, 12), 15)
    assert zone.to_server(datetime(2025, 3, 12, 12, tzinfo=UTC)) == datetime(2025, 3, 12, 15)


@pytest.mark.parametrize("instant", [datetime(2025, 7, 1, 12), 1751371200])
def test_server_side_conversions_need_an_aware_datetime(instant):
    with pytest.raises(TypeError, match="aware"):
        ServerZone.us().server_timestamp(instant)
    with pytest.raises(TypeError, match="aware"):
        ServerZone.us().to_server(instant)
