# servomexlib — Hardware Test Day (bench 4100D)

> A bench script to exercise the **entire read surface** of `servomexlib` against the
> connected **SERVOPRO 4100D**, across all three communication modes, and to land an
> opt-in `-m hardware` test suite. **Calibration writes are explicitly out of scope** —
> we only *read* cal state and verify the write gates fire before any byte is sent.
>
> Companion to [`design.md`](design.md).
> Device facts are pinned in memory (`servomex-modbus-validation`,
> `servomex-continuous-checksum`, `dev-env-crowdstrike-serial`).

---

## 0. Bench facts (confirmed 2026-05-30)

| Fact | Value |
|---|---|
| Port | **COM11** |
| Transport | RS485, **19200 8-N-1**, no flow control |
| Modbus slave address | **30** (⚠️ not `1` — the stateful test's default is wrong for this unit) |
| Populated channels | I1 O₂, I2 CO, I3 CO₂; I4 present but `0.0`/unpopulated; E1/E2 idle `0.0 mA`, name `\|\|\|\|\|\|` |
| Expected values (idle) | O₂ ≈ **20.378 %**, CO ≈ **0.084 %**, CO₂ ≈ **0.250 %** |
| Float format | IEEE-754 float32, `WordOrder.HIGH_LOW` + `ByteOrder.BIG` |
| Charset gotcha | CO₂ name = `43 4F 82 20 20 20` (`0x82` = subscript-2 glyph); continuous mode sends ASCII `"CO2"` |
| RS485 quirk | **first transaction after open/idle times out once, succeeds on retry** (turnaround) |

### How we drive the hardware on this machine

CrowdStrike blocks the **PowerShell tool** from spawning serial scripts. Use the **Bash
tool → Python** path with the project's own venv, which already has the full stack
(`servomexlib` editable + `anymodbus 0.2.0` + `anyserial 0.1.2`):

```bash
SERVOMEXLIB_ENABLE_HARDWARE_TESTS=1 \
SERVOMEXLIB_HARDWARE_PORT=COM11 SERVOMEXLIB_HARDWARE_ADDRESS=30 \
  .venv/Scripts/python.exe -m pytest tests/hardware -m hardware -v
```

Ad-hoc probes go in `scripts/` as anyio `.py` files and run the same way
(`.venv/Scripts/python.exe scripts/foo.py`). Never the PowerShell tool for serial.

### Pre-flight checklist

- [ ] Unit powered, warmed up, sampling normal process/zero gas (note what it's reading).
- [ ] COM11 free (close any terminal/Servomex tool holding the port — `exclusive=True`).
- [ ] Confirm the unit's current comm mode (front panel) — Sessions A vs B need different modes.
- [ ] `git status` clean-ish; create a branch for the day's fixtures + test additions.
- [ ] One smoke probe succeeds: `scripts/probe_modbus.py` reads I1≈20.378.

---

## Scope: what we run vs. what we never run

| ✅ Fair game (read-only / non-destructive) | ⛔ Out of scope (mutates device) |
|---|---|
| `identify()`, `poll()`, `read_channel()`, `read_all()` | `start_calibration(group, confirm=True)` — **injects cal gas** |
| `status()`, `analyser_status()`, `snapshot()` | `stop_calibration(confirm=True)` |
| `calibration_status(group)` — read-only progress poll | Any front-panel cal trigger |
| FC08 `loopback()` — diagnostic echo, non-destructive | |
| FC01 coil **readback** — read-only | |
| `stream()` / `record()` — passive or polled acquisition | |

We **do** test that `start_calibration()`/`stop_calibration()` raise
`ServomexConfirmationRequiredError` *without* `confirm=True` — that gate fires **before any
I/O**, so it never touches the device. We leave `tests/hardware/test_hardware_stateful.py`
deselected the entire day.

---

## Session A — Modbus RTU (primary, address 30)

Default state of the bench unit. This is the deepest surface — most of the day lives here.

### A1. Identify & topology
- `identify()` → assert model, populated slots (I1–I3 named, I4 present, E1/E2 idle).
- Verify **charset decode**: CO₂ name renders with the subscript glyph (`0x82` → `₂`), not garbage; unit fields NUL-stripped (` % `).
- Confirm `DeviceInfo.address == 30`, protocol `MODBUS_RTU`, serial settings echo 19200 8-N-1.
- Check `capabilities` includes `READ_CHANNELS | READ_STATUS | IDENTIFY | AUTOCAL | LOOPBACK`.

### A2. One-shot reads
- `poll()` → a full `Frame`; assert O₂≈20.378, CO≈0.084, CO₂≈0.250 (cross-checks the continuous capture), I4 `0.0`, E1/E2 `0.0 mA`.
- `read_all()` → dict keyed by every `ChannelId`; same values.
- `read_channel("I1".."E2")` for each populated slot — targeted 7-register read matches `poll()`.
- `snapshot()` → returns the cached frame with **no I/O**; raises `ServomexValidationError` before the first poll.

### A3. Status decode (the per-kind exceptions matter here)
- `status(ch)` for each channel; `analyser_status()`.
- Confirm transducer flags (`fault/maintenance/calibrating/warming_up/alarms[0..3]`) read sane.
- **E1/E2:** bit 0 is `Invalid`, **not** `fault` — assert `ChannelStatus.ok` is computed correctly for an idle analogue input.
- **D-channels:** flags are copies of the parent transducer — assert they aren't double-counted as a second analyser fault.
- `analyser_status().clock` — record the device's own date/time (may be unset).

### A4. Read-only calibration status
- `calibration_status(group)` for groups **1–4** → `CalibrationProgress(group, active, phase)`.
- Expect `active=False`, `phase=IDLE` on an idle unit. **No start/stop.**

### A5. Diagnostics (non-destructive)
- FC08 `loopback(b"\x00\x00")` and a non-trivial payload → assert echo matches.
- `servomex-diag` loopback / tap / jitter CLIs against COM11 (gated via `_gate.py`).

### A6. RS485 cold-start behaviour
- Open fresh, immediately `poll()` → observe the **first-transaction timeout-then-retry**; confirm the client's retry policy absorbs it and the call ultimately succeeds.
- Record actual latency so we can tune `TimingConfig`/`RetryPolicy` defaults.

### A7. Streaming & recording
- `stream(rate_hz=2)` (POLL mode) → iterate ~20 samples; confirm long-format `Sample`s with `requested_at`/`latency_s` populated (active mode).
- `record(anz, rate_hz=2, duration=30)` → pipe into a `SqliteSink` **and** a `CsvSink`; assert row counts and inspect `AcquisitionSummary`: `samples_emitted`, `samples_late`, `max_drift_ms`, `tick_duration_ms_p50/p99`. Confirm drift stays small at 2 Hz.
- Try a higher rate (5–10 Hz) to find where drift/late-ticks climb — record the ceiling.

### A8. Manager (single device, multidrop contract)
- `ServomexManager` → `add("a1", "COM11", protocol=MODBUS_RTU, address=30)`.
- `poll_samples()` and `poll()` return wrapped `DeviceResult`s with `ok=True`.
- `execute_each(lambda a: a.identify())` works.
- (If a second addressable unit is available, add it and confirm same-port serialisation; otherwise note as untested.)

### A9. Sync facade parity
- `Servomex.open("COM11", protocol="modbus_rtu", address=30)` → `poll()`, `read_all()`, `calibration_status()` return the same data as async.

### A10. CLIs against hardware
- `servomex-read --port COM11 --protocol modbus_rtu --address 30` → identify + one frame.
- `servomex-stream` → live samples.
- `servomex-discover` → reports `MODBUS_RTU` at addr 30.
- `servomex-capture` → records to a sink at a fixed cadence.

### A11. Error-path reads (no device mutation)
- `start_calibration(1)` **without** `confirm` → `ServomexConfirmationRequiredError` (pre-I/O; verify nothing was written).
- `read_channel("Z9")` / bad id → `ServomexValidationError`.
- Open at a **wrong address** (e.g. 31) → `ServomexTimeoutError` with context (`port`, `address`, `function_code`).

---

## Session B — Continuous ASCII (front-panel switch to broadcast)

⚠️ Requires reconfiguring the analyser's comms to **continuous broadcast** mode on the front
panel (the three modes are mutually exclusive on one port). Note the menu path used.

### B1. Capture a fresh fixture first
- Raw-capture ~10 broadcast frames to `tests/fixtures/captures/continuous_4100_<state>.bin` for regression. (The repo already ships a 5-channel idle capture; add a current-state one.)

### B2. Reads from the broadcast cache
- `open_device("COM11", protocol=CONTINUOUS_ASCII)` (or `open_continuous`).
- `poll()` returns the **latest** cached frame immediately; `read_channel`/`read_all` consistent.
- `poll(wait_fresh=True)` blocks for the **next** broadcast.
- `identify()` derived from the first frame (populated slots, names, units) — note CO₂ arrives as ASCII `"CO2"` here, not the subscript glyph.
- `capabilities` = `READ_CHANNELS | READ_STATUS | IDENTIFY` (**no** AUTOCAL/LOOPBACK).

### B3. Passive streaming & resilience
- `stream()` (AUTOPRINT) → passive subscribe; `Sample.requested_at`/`latency_s` are `None`.
- Observe `dropped_frames` over a few minutes; if any bad frame occurs, confirm it surfaces as an **error `Sample`** and the loop survives (don't force corruption on the live unit — just watch).
- Multi-subscriber fan-out: two concurrent `stream()` iterators both receive frames.

### B4. Capability gate
- `start_calibration(1, confirm=True)` in continuous mode → `ServomexProtocolUnsupportedError` (AUTOCAL absent), raised before I/O.

---

## Session C — AUTO detection (and ASCII open item)

### C1. AUTO ladder, both modes
- With the unit in **Modbus** mode: `open_device("COM11", protocol=AUTO, address=30)` → resolves `MODBUS_RTU` (FC08 loopback probe, allow for the 2-try RS485 turnaround).
- Switch unit to **continuous** mode: `open_device("COM11", protocol=AUTO)` → resolves `CONTINUOUS_ASCII` after the passive-listen window.
- Silent/wrong setup → `ServomexConnectionError` ("no recognised protocol") with context listing what was tried.
- Record the actual sniff timings to finalise the §13.5 try-count / listen-window defaults.

### C2. Modbus ASCII (open item §13.4 — only if time + unit supports it)
- If the front panel offers a **Modbus ASCII** comm mode, switch to it and repeat A1–A4 with `protocol=MODBUS_ASCII` (`anymodbus 0.2.0` now ships ASCII/LRC).
- This closes the one outstanding live-validation gap. If the unit can't do ASCII, log it as still-deferred.

---

## Deliverables (end of day)

1. **New read-only hardware suite** — `tests/hardware/test_hardware_reads.py`, marked
   `@pytest.mark.hardware`, gated on `SERVOMEXLIB_ENABLE_HARDWARE_TESTS=1`, defaulting
   `SERVOMEXLIB_HARDWARE_ADDRESS=30`. Covers A1–A5, B2–B4, C1. (The gating conftest and
   stateful suite already exist; only the `-m hardware` reads file is missing.)
2. **Fresh continuous fixture(s)** committed under `tests/fixtures/captures/`.
3. **A results log** (e.g. `docs/hardware-runs/2026-06-DD.md`): every value read, every
   assertion outcome, latencies, drift numbers, the streaming rate ceiling.
4. **Deltas back into memory/design** — any correction to the register map, charset glyphs,
   per-kind status decode, or RS485 timing → update `servomex-modbus-validation` and the
   design doc.
5. **Open-item closures** — mark §13.4 (live ASCII) resolved or still-deferred; land §13.5
   AUTO timeout defaults from C1's measured numbers.

## Reminders / risks

- The unit is **live process equipment** — don't switch comm modes or yank the port mid-cal
  if the unit happens to be auto-calibrating (`calibration_status` will show it).
- `exclusive=True` means only one process owns COM11 — close prior sessions between Session A/B/C.
- Switching comm modes is a **front-panel** operation; the library can't do it. Budget time
  for the menu round-trips between sessions.
- Keep `tests/hardware/test_hardware_stateful.py` **deselected all day** (no `-m
  hardware_stateful`, leave `SERVOMEXLIB_ENABLE_STATEFUL_TESTS` unset).
</content>
</invoke>
