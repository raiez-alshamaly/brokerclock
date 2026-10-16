# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-10-16

### Added

- `ServerZone`: a broker server clock as an IANA time zone plus a shift in 15-minute steps, with the constructors
  `us()`, `eu()`, `au()` and `fixed()`, and the conversions `offset_at()`, `to_utc()`, `to_server()` and
  `server_timestamp()`. The repeated hour is resolved by an explicit `ambiguous` policy; the skipped hour raises.
- `detect()`: finds the server clock from M1 to H1 bar history alone, using the Friday 17:00 New York weekly
  close, and reports the evidence: matched weeks, outliers, unexplained closes and tied candidates.
- `measure_offset()` and `check()`: measure the current offset from a fresh tick, or confirm a zone against live
  ticks, refusing stale ticks instead of guessing.
- `brokerclock.pandas`: `to_utc()`, `frame_to_utc()` and `to_server_timestamps()` for whole columns and for the
  arrays MetaTrader's `copy_rates_*` and `copy_ticks_*` return.
- Errors with the common base `BrokerClockError`.

[0.1.0]: https://github.com/raiez-alshamaly/brokerclock/releases/tag/v0.1.0
