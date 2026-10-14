import random
from datetime import date, datetime, timedelta, timezone

import pytest

import oracle
from brokerclock import Detection, NotEnoughData, ServerZone, WeekClose, detect

START = date(2025, 1, 5)  # a Sunday
EIGHTEEN_MONTHS = 78  # weeks


def check_weeks(result: Detection, rule: str, base: float) -> None:
    """Every week close sits on a Friday 17:00 New York, with the offset the published rule gives."""
    for week in result.weeks:
        friday = week.friday_close_utc.date()
        assert week.friday_close_utc == datetime.fromtimestamp(oracle.new_york(friday, 17), timezone.utc)
        assert week.offset == timedelta(seconds=oracle.offset(rule, base, oracle.new_york(friday, 17)))


@pytest.mark.parametrize(
    ("rule", "base", "expected", "daily_break"),
    [
        ("US", 2, ServerZone.us(), False),
        ("US", 2, ServerZone.us(), True),
        ("US", 3, ServerZone.us(3), False),
        ("EU", 2, ServerZone.eu(), False),
        ("EU", 1, ServerZone.eu(1), True),
        ("AU", 2, ServerZone.au(), False),
        ("fixed", 0, ServerZone.fixed(0), False),
        ("fixed", 2, ServerZone.fixed(2), True),
        ("fixed", 3, ServerZone.fixed(3), False),
    ],
    ids=lambda value: str(value) if isinstance(value, ServerZone) else None,
)
def test_each_convention_over_eighteen_months(rule, base, expected, daily_break):
    bars = oracle.forex_bars(rule, base, START, EIGHTEEN_MONTHS, daily_break=daily_break)
    result = detect(bars)
    assert result.zone == expected
    assert result.candidates == (expected,)
    assert not result.ambiguous
    assert result.total == result.matched == EIGHTEEN_MONTHS - 1  # the last week has no bar after it
    assert result.outliers == ()
    assert result.unexplained == ()
    check_weeks(result, rule, base)


@pytest.mark.parametrize(
    ("rule", "base", "expected"),
    [
        ("US", 2, ServerZone.us()),
        ("EU", 2, ServerZone.eu()),
        ("AU", 2, ServerZone.au()),
        ("fixed", 0, ServerZone.fixed(0)),
        ("fixed", 5.75, ServerZone.fixed(5.75)),
    ],
    ids=lambda value: str(value) if isinstance(value, ServerZone) else None,
)
def test_conventions_with_m15_bars(rule, base, expected):
    bars = oracle.forex_bars(rule, base, START, EIGHTEEN_MONTHS, timeframe=900)
    result = detect(bars, timeframe=900)
    assert result.zone == expected
    assert result.matched == result.total == EIGHTEEN_MONTHS - 1
    check_weeks(result, rule, base)


def test_summer_only_data_is_a_tie_between_us_and_eu():
    bars = oracle.forex_bars("US", 2, date(2025, 6, 1), 13)  # June to August
    result = detect(bars)
    assert result.zone is None
    assert result.ambiguous
    assert ServerZone.us() in result.candidates
    assert ServerZone.eu() in result.candidates
    assert result.matched == result.total == 12
    assert result.outliers == ()


def test_data_without_a_switch_cannot_separate_fixed_from_switching():
    bars = oracle.forex_bars("fixed", 2, date(2025, 11, 9), 12)  # November to January: winter in US and EU
    result = detect(bars)
    assert result.ambiguous
    assert {ServerZone.fixed(2), ServerZone.us(), ServerZone.eu()} <= set(result.candidates)


def test_holiday_closes_are_reported_and_do_not_change_the_result():
    first_sunday = date(2025, 7, 6)
    early_closes = {
        date(2025, 11, 23): (date(2025, 11, 28), 13, 0),  # Friday after Thanksgiving: closes at 13:00 New York
        date(2026, 12, 20): (date(2026, 12, 24), 13, 0),  # Christmas Friday: the week ends on Thursday
    }
    bars = oracle.forex_bars("US", 2, first_sunday, EIGHTEEN_MONTHS, early_closes=early_closes)
    result = detect(bars)
    assert result.zone == ServerZone.us()
    assert result.total == EIGHTEEN_MONTHS - 2  # the Thursday close fits no Friday
    assert result.matched == result.total - 1
    [outlier] = result.outliers
    assert outlier.friday_close_utc.date() == date(2025, 11, 28)
    assert outlier.offset == timedelta(hours=-2)  # +2, minus the 4 hours it closed early
    christmas_close = oracle.new_york(date(2026, 12, 24), 13) + 2 * oracle.HOUR
    assert result.unexplained == (christmas_close,)


def test_missing_weeks():
    skip = frozenset({date(2026, 2, 1), date(2026, 2, 8), date(2026, 2, 15)})
    bars = oracle.forex_bars("EU", 2, START, EIGHTEEN_MONTHS, skip=skip)
    result = detect(bars)
    assert result.zone == ServerZone.eu()
    assert result.total == result.matched == EIGHTEEN_MONTHS - 1 - len(skip)
    assert result.unexplained == ()


def test_week_ending_at_16_55_with_m5_bars():
    bars = oracle.forex_bars("US", 2, START, 52, timeframe=300, close=(16, 55), daily_break=True)
    result = detect(bars, timeframe=300)
    assert result.zone == ServerZone.us()
    assert result.matched == result.total == 51
    assert all(week.server_close % 3600 == 55 * 60 for week in result.weeks)


@pytest.mark.parametrize(
    ("timeframe", "close", "weeks"),
    [
        (60, (16, 59), 30),  # M1: 30 weeks from January still include the March mismatch weeks
        (60, (16, 47), 30),  # 13 minutes early: the earliest whole-minute close that is read correctly
        (300, (16, 55), 52),
        (300, (16, 50), 52),
        (3600, (17, 0), 52),
        (3600, (16, 40), 52),  # the last H1 bar still ends at 17:00
    ],
)
def test_brokers_closing_up_to_13_minutes_early_are_read_correctly(timeframe, close, weeks):
    bars = oracle.forex_bars("US", 2, START, weeks, timeframe=timeframe, close=close)
    result = detect(bars, timeframe=timeframe)
    assert result.zone == ServerZone.us()
    assert result.matched == result.total == weeks - 1
    check_weeks(result, "US", 2)


@pytest.mark.parametrize(("timeframe", "close"), [(300, (16, 40)), (60, (16, 46))])
def test_broker_closing_14_minutes_or_more_early_reads_15_minutes_off(timeframe, close):
    # Documented limit: offsets are rounded up to 15 minutes, so a close 14 or more minutes early falls short.
    weeks = 30
    bars = oracle.forex_bars("US", 2, START, weeks, timeframe=timeframe, close=close)
    result = detect(bars, timeframe=timeframe)
    assert result.zone == ServerZone("America/New_York", timedelta(hours=6, minutes=45))
    assert str(result.zone) == "GMT+1:45/+2:45 (US daylight saving)"
    assert result.matched == result.total == weeks - 1
    assert result.outliers == ()


def test_a_few_weeks_closing_at_16_50_are_read_correctly():
    early = {START + timedelta(weeks=n): (START + timedelta(weeks=n, days=5), 16, 50) for n in (10, 30)}
    bars = oracle.forex_bars("US", 2, START, 52, timeframe=300, early_closes=early)
    result = detect(bars, timeframe=300)
    assert result.zone == ServerZone.us()
    assert result.matched == result.total == 51
    assert result.outliers == ()


def test_a_few_weeks_closing_at_16_40_become_outliers():
    early = {START + timedelta(weeks=n): (START + timedelta(weeks=n, days=5), 16, 40) for n in (10, 30)}
    bars = oracle.forex_bars("US", 2, START, 52, timeframe=300, early_closes=early)
    result = detect(bars, timeframe=300)
    assert result.zone == ServerZone.us()
    assert result.matched == result.total - 2
    assert [week.offset for week in result.outliers] == [timedelta(hours=2, minutes=45)] * 2


@pytest.mark.parametrize("weeks", [0, 1, 2])
def test_fewer_than_two_weekly_closes_raises(weeks):
    bars = oracle.forex_bars("US", 2, START, weeks)
    with pytest.raises(NotEnoughData, match="need 2"):
        detect(bars)


def test_two_weekly_closes_are_enough():
    result = detect(oracle.forex_bars("US", 2, START, 3))
    assert result.total == 2
    assert all(isinstance(week, WeekClose) for week in result.weeks)


def test_closes_that_fit_no_friday_do_not_count():
    bars = oracle.forex_bars("US", 2, START, 1)
    gap = 3 * oracle.DAY
    midweek = [bars[-1] + gap + n * 3600 for n in range(10)]  # data resumes three days later, mid-week
    with pytest.raises(NotEnoughData, match="1 more do not"):
        detect(bars + midweek + [midweek[-1] + gap])


def test_order_and_duplicates_do_not_matter():
    bars = oracle.forex_bars("US", 2, START, 40)
    shuffled = bars + bars[:500]
    random.Random(7).shuffle(shuffled)
    assert detect(shuffled) == detect(bars)


def test_accepts_numpy_arrays():
    np = pytest.importorskip("numpy")
    bars = oracle.forex_bars("US", 2, START, 40)
    assert detect(np.array(bars, dtype=np.int64)) == detect(bars)


def test_rejects_non_integer_times():
    with pytest.raises(TypeError):
        detect([1736121600.0, 1736125200.0])


@pytest.mark.parametrize("timeframe", [59, 3601, 14400, 86400, 0, -3600, 3600.0, True, "3600", None])
def test_rejects_bad_timeframes(timeframe):
    with pytest.raises(ValueError, match="timeframe"):
        detect([], timeframe)
