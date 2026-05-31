# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Project scaffolding cloned from the `*lib` family skeleton: packaging
  (hatchling + hatch-vcs), CI/release/docs workflows, ruff/mypy/pyright/pytest
  configuration, pre-commit hooks, and documentation skeleton.
- Core types: the `ServomexError` hierarchy + `ErrorContext`, all enums, frozen
  `dataclass(slots=True)` models (`Reading`, `Frame`, `Sample`, `DeviceInfo`, …),
  the continuous-ASCII checksum + pure `parse_frame`, and the `servomex-decode`
  CLI.
- Transport layer (`SerialTransport` over `anyserial`, `FakeTransport`) and the
  passive continuous-mode client with background receive loop, latest-frame
  cache, and `stream()` fan-out.
- Modbus profile: the channel registry spine, PDU address math, the semantic
  client over `anymodbus` bound to our `Transport` (RTU + ASCII), charset
  routing, and a `MockSlave`-backed byte-accurate fake.
- `open_device(...)` entry point, the protocol-neutral session + gate ladder,
  the `Analyzer` facade, the `AUTO` sniff ladder, discovery, and the
  `Servomex.open(...)` sync facade.
- Safety-gated autocalibration control (`start_calibration` / `stop_calibration`
  / `calibration_status`) with coil-pulse semantics and `confirm=True` +
  capability gates; opt-in hardware-stateful tests.
- `ServomexManager` (RS485 multidrop), the drift-free `record()` recorder +
  `OverflowPolicy` + `AcquisitionSummary`, the sink set (memory / CSV / JSONL /
  SQLite, plus optional Parquet / Postgres) with `pipe()`, the `servomex-read`,
  `servomex-stream`, `servomex-discover`, `servomex-capture`, and `servomex-diag`
  CLIs, and the sync `SyncManager` / `SyncSink` / `record_to_sink` wrappers.

### Deferred

- **Live ASCII-mode validation** against the bench 4100D (design §13.4): Modbus
  RTU and continuous-ASCII are hardware-validated; the byte-accurate `MockSlave`
  fake covers ASCII framing in CI, but a live read against the unit switched to
  Modbus ASCII is still pending. Tracked in [`docs/design.md`](docs/design.md)
  §13.4; the bench procedure lives in
  [`docs/hardware-test-day.md`](docs/hardware-test-day.md).

[Unreleased]: https://github.com/GraysonBellamy/servomexlib/commits/main
