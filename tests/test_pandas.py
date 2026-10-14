from datetime import date

import pytest

import oracle

np = pytest.importorskip("numpy")
pd = pytest.importorskip("pandas")

import brokerclock.pandas as bcpd  # noqa: E402
from brokerclock import ServerZone  # noqa: E402

try:  # pandas 2 raises pytz's errors for ambiguous and skipped times; pandas 3 raises ValueError
    from pytz.exceptions import InvalidTimeError

    TZ_ERRORS: tuple[type[Exception], ...] = (ValueError, InvalidTimeError)
except ImportError:
    TZ_ERRORS = (ValueError,)

# The structured array that MetaTrader's copy_rates_* functions return.
RATES = np.dtype(
    [
        ("time", "<i8"),
        ("open", "<f8"),
        ("high", "<f8"),
        ("low", "<f8"),
        ("close", "<f8"),
        ("tick_volume", "<u8"),
        ("spread", "<i4"),
        ("real_volume", "<u8"),
    ]
)


def timestamps(utc_seconds: list[int]) -> list[pd.Timestamp]:
    return [pd.Timestamp(t, unit="s", tz="UTC") for t in utc_seconds]


def rates_array(server_times: list[int]) -> np.ndarray:
    rates = np.zeros(len(server_times), dtype=RATES)
    rates["time"] = server_times
    rates["open"] = rates["high"] = rates["low"] = rates["close"] = 1.08
    rates["tick_volume"] = 100
    return rates


# H1 bars through the US spring switch and the following mismatch week, as UTC instants and as server times.
UTC_HOURS = list(range(oracle.epoch(date(2025, 3, 6)), oracle.epoch(date(2025, 3, 15)), 3600))
SERVER_HOURS = [t + oracle.offset("US", 2, t) for t in UTC_HOURS]


def test_frame_to_utc_with_a_rates_array():
    rates = rates_array(SERVER_HOURS)
    frame = bcpd.frame_to_utc(rates, ServerZone.us())
    assert list(frame.columns) == list(RATES.names)
    assert frame["time"].tolist() == timestamps(UTC_HOURS)
    assert str(frame["time"].dt.tz) == "UTC"
    assert (frame["close"] == 1.08).all()
    assert rates["time"].tolist() == SERVER_HOURS  # the input is untouched


def test_frame_to_utc_returns_a_copy_of_a_dataframe():
    source = pd.DataFrame(rates_array(SERVER_HOURS))
    frame = bcpd.frame_to_utc(source, ServerZone.us())
    assert frame is not source
    assert source["time"].tolist() == SERVER_HOURS
    assert frame["time"].tolist() == timestamps(UTC_HOURS)


def test_frame_to_utc_with_tick_milliseconds():
    ticks = pd.DataFrame({"time_msc": [s * 1000 + 250 for s in SERVER_HOURS], "bid": 1.08})
    frame = bcpd.frame_to_utc(ticks, ServerZone.us(), column="time_msc", unit="ms")
    assert frame["time_msc"].tolist() == [t + pd.Timedelta(milliseconds=250) for t in timestamps(UTC_HOURS)]


def test_to_utc_keeps_the_input_shape():
    series = pd.Series(SERVER_HOURS, index=range(100, 100 + len(SERVER_HOURS)))
    as_series = bcpd.to_utc(series, ServerZone.us())
    assert isinstance(as_series, pd.Series)
    assert as_series.index.equals(series.index)
    as_index = bcpd.to_utc(np.array(SERVER_HOURS), ServerZone.us())
    assert isinstance(as_index, pd.DatetimeIndex)
    assert as_index.tolist() == as_series.tolist() == timestamps(UTC_HOURS)


# A 24/7 symbol on an EU-rule GMT+2/+3 server, M15 bars through the autumn switch: 03:00-03:45 server time repeats.
UTC_QUARTERS = list(range(oracle.epoch(date(2025, 10, 25), 22), oracle.epoch(date(2025, 10, 26), 4), 900))
SERVER_QUARTERS = [t + oracle.offset("EU", 2, t) for t in UTC_QUARTERS]


def test_repeated_hour_in_sorted_24_7_data_is_inferred():
    assert len(set(SERVER_QUARTERS)) == len(SERVER_QUARTERS) - 4
    result = bcpd.to_utc(pd.Series(SERVER_QUARTERS), ServerZone.eu())
    assert result.tolist() == timestamps(UTC_QUARTERS)


def test_repeated_hour_that_cannot_be_inferred_raises():
    repeated = oracle.epoch(date(2025, 10, 26), 3, 30)
    with pytest.raises(TZ_ERRORS):
        bcpd.to_utc([repeated], ServerZone.eu())
    assert bcpd.to_utc([repeated], ServerZone.eu(), ambiguous="NaT").isna().all()


def test_skipped_hour_raises():
    skipped = oracle.epoch(date(2025, 3, 30), 3, 30)  # London 01:30 does not exist that day
    with pytest.raises(TZ_ERRORS):
        bcpd.to_utc([skipped], ServerZone.eu())
    assert bcpd.to_utc([skipped], ServerZone.eu(), nonexistent="NaT").isna().all()


def test_to_server_timestamps_round_trip():
    zone = ServerZone.us()
    utc = pd.date_range("2025-01-01", "2027-01-01", freq="h", tz="UTC", inclusive="left")
    utc_seconds = [oracle.epoch(date(2025, 1, 1)) + 3600 * n for n in range(len(utc))]
    server = bcpd.to_server_timestamps(utc, zone)
    assert server.dtype == np.int64
    assert server.tolist() == [t + oracle.offset("US", 2, t) for t in utc_seconds]
    assert bcpd.to_utc(server, zone).tolist() == utc.tolist()
    assert bcpd.to_server_timestamps(pd.Series(utc), zone).tolist() == server.tolist()


def test_to_server_timestamps_floors_and_accepts_any_aware_zone():
    tokyo = pd.DatetimeIndex([pd.Timestamp("2025-07-01 18:00:00.75", tz="Asia/Tokyo")])
    assert bcpd.to_server_timestamps(tokyo, ServerZone.us()).tolist() == [oracle.epoch(date(2025, 7, 1), 12)]


def test_to_server_timestamps_needs_aware_times():
    with pytest.raises(TypeError, match="aware"):
        bcpd.to_server_timestamps(pd.DatetimeIndex(["2025-07-01 09:00"]), ServerZone.us())
