# brokerclock — MetaTrader broker server time to UTC (and back), with daylight saving done right

[![PyPI](https://img.shields.io/pypi/v/brokerclock)](https://pypi.org/project/brokerclock/)

MetaTrader 5 does not give you UTC. The `time` and `time_msc` values that `copy_rates_*`, `copy_ticks_*`,
`history_deals_get` and `history_orders_get` return are the broker's **server clock written as epoch seconds**,
even though they look like Unix timestamps. If you searched for "MT5 server time UTC", "broker GMT offset" or
"copy_rates time zone", this is why your bars are two or three hours off.

- Most brokers run **GMT+2 in winter and GMT+3 in summer, switching with US daylight saving**: New York time plus
  7 hours, so the forex day starts at server midnight, 17:00 in New York.
- A fixed offset breaks for weeks every year. `+3` is wrong all winter, and even a European daylight-saving rule is
  wrong for about three weeks in March and one week around the start of November, when the US and Europe switch
  on different dates.
- The server clock works in the other direction too: the date arguments of `copy_rates_range` and friends, and an
  order's `expiration`, are read as server time.

brokerclock models the server clock as an IANA time zone plus a shift, so every conversion uses the rule in force at
that moment. It finds the rule from price history alone, confirms it against live ticks, and converts single values
or whole pandas columns. It needs only the standard library (plus `tzdata` on Windows) and never imports
`MetaTrader5`, so it runs and tests on any operating system.

## Install

brokerclock is on [PyPI](https://pypi.org/project/brokerclock/):

```bash
pip install brokerclock
pip install "brokerclock[pandas]"   # with the pandas helpers
```

Python 3.10 or newer.

## Quick start

### 1. Find the broker's clock from H1 EURUSD bars

```python
import MetaTrader5 as mt5

import brokerclock

mt5.initialize()

# About a year of H1 bars of a forex major.
rates = mt5.copy_rates_from_pos("EURUSD", mt5.TIMEFRAME_H1, 0, 6500)
result = brokerclock.detect(rates["time"], timeframe=3600)
print(result.zone)  # GMT+2/+3 (US daylight saving)
print(f"{result.matched} of {result.total} weeks agree")
zone = result.zone
```

If `result.ambiguous` is true, the bars cannot separate the clocks in `result.candidates` (summer-only data, for
example): fetch a longer span. Running the same check on a second major is good practice. Once known, the clock is a
constant you can keep in your configuration, such as `brokerclock.ServerZone.us()`.

### 2. Convert bars to UTC

```python
import brokerclock.pandas as bcpd

bars = bcpd.frame_to_utc(rates, zone)  # a DataFrame whose "time" column is aware UTC
print(bars[["time", "close"]].tail(3))

first_bar = zone.to_utc(rates["time"][0])  # one value, without pandas: an aware UTC datetime
```

### 3. Query by UTC and set an order expiration

```python
from datetime import datetime, timedelta, timezone

# Bars of March 2025 in UTC terms. Pass server-clock ints: a naive datetime would go through the PC's time zone.
start = datetime(2025, 3, 1, tzinfo=timezone.utc)
end = datetime(2025, 4, 1, tzinfo=timezone.utc)
march = mt5.copy_rates_range("EURUSD", mt5.TIMEFRAME_H1, zone.server_timestamp(start), zone.server_timestamp(end))

# A pending order that expires four hours from now.
expiry = datetime.now(timezone.utc) + timedelta(hours=4)
request = {
    "action": mt5.TRADE_ACTION_PENDING,
    "symbol": "EURUSD",
    "volume": 0.1,
    "type": mt5.ORDER_TYPE_BUY_LIMIT,
    "price": 1.05,
    "type_time": mt5.ORDER_TIME_SPECIFIED,
    "expiration": zone.server_timestamp(expiry),
}
mt5.order_send(request)
```

### 4. Check the clock against live ticks

```python
tick = mt5.symbol_info_tick("EURUSD")
if brokerclock.check(zone, [tick.time_msc / 1000]):
    print("live ticks agree with", zone)
else:
    print("no fresh tick to confirm it: the market is closed, or the zone is ahead of the server")

# Without a known zone: the current offset, while the market trades.
print(brokerclock.measure_offset([tick.time_msc / 1000]))  # 3:00:00 in summer, 2:00:00 in winter
```

`check` raises `ZoneMismatchError` when the tick would be from the future under that zone (the zone is behind the
server, or the PC clock is behind). `measure_offset` raises `StaleTickError` or `OffsetOutOfRange` instead of
guessing when the tick is not fresh.

## Broker clock conventions

| Server clock | Winter | Summer | Switch dates | `ServerZone` |
|---|---|---|---|---|
| New York close, the most common | GMT+2 | GMT+3 | US: second Sunday of March, first Sunday of November | `ServerZone.us()` |
| EU rule | GMT+2 | GMT+3 | EU: last Sundays of March and October | `ServerZone.eu()` |
| EU rule, central European | GMT+1 | GMT+2 | EU | `ServerZone.eu(1)` |
| Australian rule | GMT+2 (April to October) | GMT+3 (October to April) | AU: first Sundays of October and April | `ServerZone.au()` |
| Fixed GMT | GMT+0 | GMT+0 | none | `ServerZone.fixed(0)` |
| Fixed GMT+2 | GMT+2 | GMT+2 | none | `ServerZone.fixed(2)` |
| Any other clock | | | | `ServerZone.us(3)`, `ServerZone.fixed(5.75)`, `ServerZone("Asia/Tokyo")`, `ServerZone("Europe/Berlin", timedelta(hours=1))` |

A `ServerZone` is the wall clock of an IANA zone plus a shift in 15-minute steps; `str(zone)` describes it, for
example `GMT+2/+3 (US daylight saving)`.

## How detection works

1. A gap of 40 hours or more after a bar is a weekend; that bar's end is the weekly close on the server clock.
2. The forex week ends on Friday at 17:00 New York, so each close gives the server's offset that week. Brokers close
   at or before 17:00, never after, so the offset is rounded up to the next 15 minutes.
3. Every candidate clock (fixed, or US, EU or Australian daylight saving, at any base offset) predicts each week.
4. The clock that predicts the most weeks wins; the weeks it misses are `outliers` (early holiday closes), and closes
   that fit no Friday are `unexplained`.
5. A tie means the data cannot tell the clocks apart, and `zone` is `None`: use a longer span.

Detection needs a forex major and bars from M1 to H1 (`timeframe` 60 to 3600 seconds; longer bars are rejected
because they end on the server's own 4-hour or day boundaries and hide the switch weeks). Use M15 or shorter for
clocks with 30- or 45-minute offsets. The data should cover at least six months, or a span that includes March or
late October to early November.

## API

| Name | Purpose |
|---|---|
| `ServerZone(zone, shift)`, `.us()`, `.eu()`, `.au()`, `.fixed()` | the server clock |
| `zone.to_utc(server_time, ambiguous="raise")` | server epoch seconds or naive server datetime to aware UTC |
| `zone.server_timestamp(instant)`, `zone.to_server(instant)` | aware instant to server epoch int, or naive server datetime |
| `zone.offset_at(instant)` | the server's UTC offset at an aware instant |
| `detect(bar_times, timeframe=3600)` | the clock from M1 to H1 bar history, as a `Detection` |
| `measure_offset(server_times, now=None, tolerance=60)` | the current offset from a fresh tick and the PC clock |
| `check(zone, server_times, now=None, tolerance=60, max_age=300)` | whether live ticks agree with a zone |
| `brokerclock.pandas`: `to_utc`, `frame_to_utc`, `to_server_timestamps` | the same conversions for whole columns |

The library's own errors share the base class `BrokerClockError`, a `ValueError`: `AmbiguousTimeError`,
`NonexistentTimeError`, `NotEnoughData`, `StaleTickError`, `OffsetOutOfRange` and `ZoneMismatchError`. Invalid
arguments raise a plain `ValueError` or `TypeError`.

## Limitations

- When a broker switches the clock of 24/7 symbols (crypto CFDs) is broker-specific, so bars of those symbols right
  at a switch can be an hour off.
- Metals and indices follow their own exchange hours; detect the clock from a forex major.
- Live measurement depends on the PC clock; keep automatic time sync on.
- A broker's demo and live servers can run different clocks; detect each one.
- A broker that changes its clock may rewrite its history in the new clock; detect again after a change.
- Weekly closes less than 14 minutes before 17:00 New York are handled (16:50 in M5 bars, 16:47 or later in M1
  bars). If the last bar of a week ends 14 minutes or more early (16:40, say), that week reads 15 minutes off. A
  few such weeks become `outliers`; if every week ends that way, the whole result is 15 minutes off, for example
  GMT+1:45/+2:45 instead of GMT+2/+3. H1 bars avoid this, because the last H1 bar still ends at 17:00.

## Notes

Not affiliated with MetaQuotes. MetaTrader is a trademark of MetaQuotes Ltd.

Copyright 2026 Raeez alshamaly. Licensed under the Apache License, Version 2.0; see [LICENSE](LICENSE).
