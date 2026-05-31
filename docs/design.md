# servomexlib — Architecture & Design (v2, from-scratch)

> Async-first Python driver for **Servomex SERVOPRO 4000-series gas analysers**
> (developed against a bench **4100D** on `COM11`), presenting one protocol-neutral,
> channel-oriented API that decodes the analyser's three communication modes into a
> single set of typed models.
>
> This is a **fresh, from-scratch design**.
>
> `servomexlib` is a member of the `*lib` instrument-driver family
> (`anyserial`, `sartoriuslib`, `watlowlib`, `alicatlib`, `nidaqlib`, `dtollib`).
> **Family harmony is defined at the boundary, not the core:** the public entry point,
> the frozen `StrEnum`/dataclass models, the error hierarchy, the streaming/sinks/sync/CLI
> conventions, and the *entire* tooling skeleton match the siblings exactly. The internal
> layering is shaped to this device — just as `nidaqlib`/`dtollib` (TaskSpec) diverge
> internally from `sartoriuslib` (Command/Variant). This document is the reference the code
> points to as "design §N".

---

## 1. What kind of device this is (and why it drives the design)

Everything below follows from one observation: **the 4100 is a multi-channel, read-mostly
sensor, not a richly-commanded instrument.** Reconciling this against the family template
(`sartoriuslib`) is the central design act.

| Property of the 4100 | Consequence for the design |
|---|---|
| Reports a **set of channels** every cycle (I1–I4 measured, D1–D4 derived, E1–E2 external mA), each with id/name/value/unit/status | The core domain object is the **channel**, and the central artifact is a **frame/snapshot** = a timestamped set of channel `Reading`s. One model serves all three modes. |
| Three wire modes, but only **two semantics**: an *unsolicited read-only broadcast* (Continuous ASCII) and a *polled request/response* (Modbus) — RTU vs ASCII differ **only in framing** | Two `ProtocolClient` implementations, not three. Modbus framing is a strategy (RTU=CRC-16, ASCII=LRC). |
| Continuous mode has **no request channel at all** — the analyser just emits frames every *frame-frequency* seconds | A symmetric `Command[Req,Resp]`-with-two-variants abstraction (sartorius's core) **does not fit**: the continuous "variant" would be decode-only. We drop that layer. |
| The entire control surface is **tiny**: read channels, read status, start/stop autocalibration | Semantic operations live directly on the per-protocol client interface. No command registry, no opcode tables. |
| Modes are **mutually exclusive** and set on the **front panel** | We never switch modes for the user. We are *told* the mode, or sniff it (`AUTO`). |
| RS485 **multidrop**, unique slave address per analyser | Per-call `address`; a `ServomexManager` over one bus (Modbus only — continuous is single-broadcaster). |

**Design thesis:** keep the family's *outer shell* verbatim; replace sartorius's
`commands/` core with a **channel-registry + thin per-protocol client** core that matches the
4100's actual shape.

### Goals

- One **protocol-neutral, channel-oriented** API. `poll()`, `read_channel("I1")`,
  `snapshot()`, `identify()`, `stream()`, `start_calibration(group)` behave identically
  whether the analyser is in **Continuous ASCII**, **Modbus ASCII**, or **Modbus RTU**.
- **Async core** on `anyio`; a thin **sync facade** (`Servomex.open(...)`) for scripts/REPL.
- **No hardware needed** to develop or test: a `FakeTransport` + recorded fixtures drive
  every protocol and the CLI in CI. This includes the Modbus path (see §5 — we own the bytes).
- Family harmony at the boundary: identical entry point, models style, error hierarchy,
  tooling, tests, docs.

### Non-goals

- We do **not** auto-switch the analyser's comm mode (front-panel only; `AUTO` only *sniffs*).
- We do **not** model the front-panel menu, display, relays, or analogue outputs beyond what
  the serial/Modbus profile exposes.
- We do **not** depend on `pint`. `Unit` is a local `StrEnum`; `units.to_pint()` returns
  pint-compatible strings for callers who want them.
- We do **not** depend on `pydantic`. All models are frozen `dataclass(slots=True)`.

---

## 2. Layered architecture

The same async onion as every sibling — raw bytes from `anyserial` (no framing, no Modbus;
we own all codecs) — but the **core is channel-centric**, not command-centric.

```
anyserial                      raw async serial bytes: open_serial_port, SerialPort,
                               SerialConfig, RS485Config, typed serial errors
   │
   ▼
transport/      base.py        Transport PEP-544 Protocol + SerialSettings frozen dataclass
                serial.py      SerialTransport over anyserial (read_exact / read_until /
                               read_available, anyio.fail_after timeouts, pushback buffer,
                               reopen() for re-config)
                fake.py        FakeTransport (scripted write→reply; the no-hardware seam —
                               drives BOTH continuous and Modbus paths)
   │
   ▼
protocol/       base.py        ProtocolKind StrEnum + ProtocolClient PEP-544 Protocol
                               (the small SEMANTIC surface — see §4.1) + Capability set
                detect.py      AUTO sniff ladder (§4.4)
                continuous/    client.py   passive receive loop + latest-frame cache + fan-out
                               parser.py   pure bytes → ContinuousFrame (self-describing)
                               checksum.py 16-bit additive verify/compute (§3.1)
                modbus/        client.py   the ~5 semantic ops over anymodbus
                               registers.py channel↔register address math (consumes registry)
                               session.py  anymodbus Slave bound to OUR Transport (§5)
                               errors.py   anymodbus exception → ServomexModbusError mapping
   │
   ▼
registry/       channels.py    ChannelId / ChannelKind / ChannelSpec table  ← the spine (§6)
                units.py       Unit / UnitKind enums + coercion + to_pint
                status.py      ChannelStatus + analyser-status flag decode (bitmap & ASCII)
                charset.py     Servomex display-ROM glyph → Unicode map (§3.3)
   │
   ▼
devices/        session.py     protocol-neutral dispatch + safety/capability gates +
                               background-task lifecycle (continuous receive loop) + caching
                analyzer.py    Analyzer — the public facade (async context manager)
                factory.py     async open_device(...)  ← THE entry point
                models.py      Reading / Frame / Sample / DeviceInfo / ChannelInfo /
                               ChannelStatus / AnalyserStatus / CalibrationProgress
                discovery.py   find_devices / discover_port / DiscoveryResult
                capability.py  Capability / SafetyTier / Availability
   │
   ▼
streaming/  Sample, record(), StreamMode (POLL | AUTOPRINT), AcquisitionSummary
sinks/      base, memory, csv, jsonl, sqlite (+ optional parquet, postgres)
sync/       Servomex.open(...) + SyncAnalyzer + SyncManager via anyio BlockingPortal
cli/        _common, read, stream, discover, decode, capture, diag/
manager.py  ServomexManager (RS485 multidrop, Modbus)
testing.py  FakeTransport, fixture loaders, FakeModbusSlave, canned frames
errors.py · config.py · units.py · version.py · _version.py · py.typed · __init__.py
```

**What is deliberately absent vs sartoriuslib:** the `commands/` package and the
`Command[Req,Resp]` / `XbpiVariant` / `SbiVariant` machinery. The 4100's operation set is
small and asymmetric; semantic ops live on the `ProtocolClient` interface (§4.1) and the
registry carries the addressing knowledge a command table would otherwise hold.

---

## 3. The three communication modes

All three are mutually exclusive on the wire, selected on the analyser's front panel.
`servomexlib` is told which to speak via `ProtocolKind`; `AUTO` sniffs.

```python
class ProtocolKind(StrEnum):
    AUTO = "auto"                    # sniff: probe Modbus, else passive-listen continuous (§4.4)
    CONTINUOUS_ASCII = "continuous"  # unsolicited ;-delimited frames (§3.1)
    MODBUS_RTU = "modbus_rtu"        # binary Modbus, CRC-16 framed (§3.2)
    MODBUS_ASCII = "modbus_ascii"    # ':'…hex…LRC…CRLF Modbus (§3.2)
```

### 3.1 Continuous ASCII — unsolicited broadcast (§3.4 of the manual)

**Validated against the bench 4100D** (fixture
[`tests/fixtures/captures/continuous_4100_idle_5ch.bin`](../tests/fixtures/captures/continuous_4100_idle_5ch.bin)
— 6 live frames, the reverse-engineering + parser-regression baseline):

| Parameter | Value |
|---|---|
| Baud / framing | 19200, 8 data, no parity, 1 stop, no flow control |
| Encoding | ASCII text |
| Cadence | one frame every *frame-frequency* seconds (1–9999 s; 0 = off). Observed ~2 s. |
| Channels (`N=05`) | I1 Oxygen %, I2 CO %, I3 CO2 %, E1 mA, E2 mA (this unit: 3 transducers + 2 external) |
| Idle values | O₂ ≈ 20.38 %, CO ≈ 0.08 %, CO₂ ≈ 0.25 %; E1/E2 = 0.0 mA |

One captured frame (`;`-delimited throughout; spaces significant):

```
 06-10-20;02:54:12;  ;S1S1S1S1;05;I1;Oxygen;20.376; % ;    ;  ; ; ;I2;CO    ; 0.084; % ; …
   … ;E1;||||||;   0.0; mA;    ;  ; ; ;E2;||||||;   0.0; mA;    ;  ; ; ;2A1D;<CR><LF>
```

**Frame grammar** (verified byte-for-byte against the fixture):

```
<sp> HEADER ( BLOCK ){N} CKSUM ; <CR><LF>
```

Header fields (5, `;`-delimited):

| Field | Chars | Meaning | Format |
|---|---|---|---|
| date | 8 | analyser clock date | `DD-MM-YY` (may be unset/wrong) |
| time | 8 | analyser clock time | `HH:MM:SS` |
| FM status | 2 | analyser fault / maintenance | `F`/space, `M`/space |
| autocal flags | 8 | 4 cal-groups × 2 chars: `S`(sample)/`C`(cal) + `1`/`2` (which cal gas); `S1S1S1S1` at idle |
| N | 2 | channel count | `03`–`07`; last two are external inputs E1, E2 |

Per-channel block — exactly **8** `;`-fields (verified):

| Sub | Chars | Meaning |
|---|---|---|
| id | 2 | channel id (`I1`, `D1`, `E1`, …) |
| name | 6 | display name (`Oxygen`; `\|\|\|\|\|\|` = unlabelled/idle) |
| value | 6 | measurement (right-justified) |
| unit | 3 | unit (`%`, `mA`, …) |
| alarms | 4 | one char each, alarms 1–4 (raised digit = active, space = OK) |
| FM | 2 | channel fault / maintenance — `F`/space, `M`/space |
| cal | 1 | `C` while calibrating, else space |
| warmup | 1 | `W` while warming up, else space |

Trailer: `CKSUM` = **4 uppercase hex digits**, then a closing `;`, then `CR LF`.

**Checksum — 16-bit additive (CONFIRMED** against all 6 fixture frames): sum every byte
**after** the leading start-space, up to **and including** the `;` immediately preceding the
checksum field; take `& 0xFFFF`; emit as 4 uppercase hex digits. Excluded: the leading
`0x20`, the checksum's own 4 chars, the closing `;`, and `CR LF`. The parser recomputes and
verifies; mismatch raises `ServomexChecksumError` carrying the raw bytes and both values.

**Continuous mode is read-only — there is no command channel.** `start_calibration` and any
write raise `ServomexProtocolUnsupportedError` in this mode (gated *before* any I/O, §4.3).
See §4.2 for how `poll()`/`read_channel()` behave against an unsolicited stream.

The parser is a **pure function** `parse_frame(raw: bytes) -> ContinuousFrame`: strip the
start char, split on `;`, validate field count against `N`, decode header + N blocks, verify
checksum. `||||||` names → `None`; blank status fields → cleared flags. Each block becomes a
`Reading`; the frame becomes a `Frame` (and fans out to `list[Sample]`).

### 3.2 Modbus (RTU + ASCII) — Appendix B of the manual

RTU and ASCII share **one PDU/register map**; they differ only in framing (RTU = binary +
CRC-16; ASCII = `:` + ASCII-hex + LRC + `CRLF`). One Modbus client; framing is a strategy.

**Measurement data — input registers, FC04, IEEE-754 float32, 2 regs/value, high word first**
(HW-confirmed: `WordOrder.HIGH_LOW` + `ByteOrder.BIG`; cross-checks the continuous capture
exactly — I1 20.378, I2 0.084, I3 0.250):

| Reg (1-based) | PDU addr | Datum |
|---|---|---|
| 30001–30002 | 0 | I1 value (float32) |
| 30003–30005 | 2 | I1 name (6-char string, 3 regs) |
| 30006–30007 | 5 | I1 unit (3-char string, 2 regs, trailing NUL) |
| … | | I2 @30008 (PDU 7), I3 @30015, I4 @30022 — **stride 7 regs** |
| 30029–30056 | 28 | D1–D4 derived (same 7-reg layout) |
| 30057–30070 | 56 | E1–E2 external mA (same layout) |

**Status & alarms — discrete inputs, FC02, stride 8**, bit offsets `0..7` =
`Fault, Maintenance, Calibration, WarmingUp, Alarm1, Alarm2, Alarm3, Alarm4`:
I1 @10001, I2 @10009, … E1 @10065, E2 @10073 (PDU addr = data-model − 10001). Analyser
status: `11001` Fault, `11002` Maintenance; `11009`–`11016` cal-group sample/cal & gas-1/gas-2
valve flags.

**Two per-`kind` exceptions to that uniform bit layout (Appendix B notes), handled in
`registry/status.py` — not by re-reading the bitmap uniformly:**
- **EXTERNAL_INPUT (E1/E2):** bit 0 is **`Invalid`** (not `Fault`) and bits 1–3 are reserved/0
  (no Maintenance/Calibration/WarmingUp on an analogue input). The decoder maps E-channel bit 0
  to a meaningful state so `ChannelStatus.ok` is correct for externals — it must not naively read
  bit 0 as `fault` and bits 1–3 as cleared cal/warmup.
- **DERIVED (D1–D4):** bits 0–3 are **copies of the parent transducer's** flags, not independent
  signals. The decoder/consumers treat them as derived, so a D-channel "fault" is not double-
  counted as a second analyser fault.

**Autocalibration control — coils, FC05/FC15 (read back FC01):** `00001`–`00004` start cal
group 1–4; `00009` stop all. The action triggers on a **0→1 transition** and the master must
return the coil to 0; the client models this as one `start_calibration(group)` pulse.

**Diagnostics & exceptions:** FC08 sub-function 00 = loopback (used by the `AUTO` probe and
`servomex-diag`). Modbus exception 01 → `ServomexIllegalFunctionError`; 02 →
`ServomexIllegalDataAddressError`. A CRC/LRC/frame error yields no response → surfaces as
`ServomexTimeoutError`. Modbus is question/answer only (no unsolicited traffic) → Modbus mode
uses `StreamMode.POLL`.

PDU addressing converts the 3xxxx/1xxxx/0xxxx data-model numbers to 0-based PDU addresses per
function code (`registry` holds the data-model numbers; `protocol/modbus/registers.py` does
the arithmetic).

**Read grouping (2026-05-30):** `ModbusClient` now plans block reads from the registry instead of
issuing per-field transactions. The bench unit answers FC04 `0,count=70` and FC02 `0,count=80`
successfully (`probe_out/probe_report.json`), so a populated five-channel frame uses one broad
FC04 read, one broad FC02 channel-status read, and the existing analyser-status FC02 read. Static
name/unit metadata is cached internally and refreshed by `identify()`; poll/read-channel calls
populate missing metadata automatically. If a device rejects a broad span, the client caches a
strict gap-free policy and retries so later polls stay compatible.

### 3.3 Name/unit charset — the display-ROM gotcha (HW-confirmed)

Modbus **name/unit registers use the analyser's display character ROM, not ASCII.** CO₂'s
name comes back as bytes `43 4F 82 20 20 20` where `0x82` is the subscript-2 glyph (continuous
mode substitutes plain ASCII `"CO2"`). Decoding must be **lenient and never raise**:
`registry/charset.py` owns a Servomex-glyph→Unicode table (`0x82`→`₂`, plus `°` and other
sub/superscripts as discovered) over a `latin-1` / `errors="replace"` fallback. **Both** the
Modbus codec and the continuous parser route names/units through it, so a `Reading.name` is
always clean Unicode regardless of mode. Unit fields are 3 chars + trailing NUL — strip it.

---

## 4. Protocol layer — the device-fit core

### 4.1 `ProtocolClient` — a small *semantic* interface (not a command dispatcher)

Each mode implements one structural `Protocol`. The methods are the analyser's actual
capabilities; there is no generic `execute(bytes)`/opcode layer.

```python
@runtime_checkable
class ProtocolClient(Protocol):
    kind: ProtocolKind
    capabilities: frozenset[Capability]          # READ_CHANNELS, READ_STATUS, AUTOCAL, …

    async def read_frame(self, *, timeout: float | None = None) -> Frame: ...
    #   continuous: most-recent cached frame (see §4.2); modbus: one FC04+FC02 sweep.

    async def read_channel(self, channel: ChannelId, *, timeout=None) -> Reading: ...
    #   continuous: pulled from the latest frame; modbus: targeted 7-register read.

    async def identify(self, *, timeout=None) -> DeviceInfo: ...
    #   continuous: derived from the first frame; modbus: name/unit strings per populated slot.

    # control — present only when AUTOCAL ∈ capabilities (Modbus); else NotImplemented and
    # the session's capability gate raises ServomexProtocolUnsupportedError *before* dispatch.
    async def start_calibration(self, group: int, *, timeout=None) -> None: ...
    async def stop_calibration(self, *, timeout=None) -> None: ...
    async def calibration_status(self, *, timeout=None) -> CalibrationProgress: ...

    async def aclose(self) -> None: ...
```

`Capability` is a small `Flag` (`READ_CHANNELS | READ_STATUS | IDENTIFY | AUTOCAL | LOOPBACK`).
The continuous client advertises read/identify only; the Modbus client advertises everything.
The **session** consults `capabilities` to gate, so the facade exposes one uniform method set
and unsupported operations fail cleanly per mode (§4.3).

### 4.2 Reading an unsolicited stream — the continuous-mode lifecycle

Continuous mode has no request, so `read_frame()`/`read_channel()` cannot "ask". The
`ContinuousClient` instead runs a **background receive loop** inside the `Analyzer`'s async
context:

- On `Analyzer.__aenter__`, the session starts a task in its `anyio.TaskGroup` that loops:
  `read_until(b"\r\n")` → `parse_frame` → verify checksum → store as `latest` → fan-out to any
  live `stream()` subscribers (via an `anyio` memory-object-stream broadcast).
- `poll()` / `read_frame()` return the **most recent** parsed frame immediately
  (`wait_fresh=True` forces a wait for the *next* frame instead). Before the first frame
  arrives, they wait up to `timeout` and raise `ServomexTimeoutError` if none comes (e.g.
  frame-frequency set very high, or mode mismatch).
- A bad frame (checksum/parse) increments a counter and is dropped from the cache but
  surfaced to `stream()` subscribers as an error `Sample` (resync, never crash the loop).
- `__aexit__` cancels the loop and closes the transport.

Modbus mode owns **no** background task: `read_frame()` performs a synchronous FC04+FC02
sweep; `stream(mode=POLL)` is a timed acquisition loop driven by `record()` (§7).

`StreamMode` therefore maps cleanly onto the modes: continuous → `AUTOPRINT` (passive
subscribe), Modbus → `POLL` (timed). The facade defaults the right mode for the active
protocol; passing the wrong one raises `ServomexValidationError`. *(`AUTOPRINT` is the inherited
family `StreamMode` member — sartorius SBI vocabulary — reused verbatim for boundary harmony; for
the 4100 it denotes a passive **unsolicited-broadcast** subscribe. We keep the family name rather
than mint `BROADCAST` to avoid diverging the shared `StreamMode` enum.)*

### 4.3 Session gates (pre-I/O, in order)

`devices/session.py` is the single dispatch point between facade and client. Every call walks:

1. **Safety-tier gate** — `SafetyTier.STATEFUL` ops (autocalibration) require `confirm=True`,
   else `ServomexConfirmationRequiredError` *before any byte is sent*.
2. **Capability gate** — the op's `Capability` must be in the active client's set, else
   `ServomexProtocolUnsupportedError` (e.g. `start_calibration` in continuous mode).
3. **Validation gate** — argument checks (`group ∈ 1..4`, known `ChannelId`, populated slot).

There is no opcode/availability cache to maintain (no command table); the registry already
knows which channel slots are populated, set at `identify()`.

### 4.4 `AUTO` detection ladder

Because modes are mutually exclusive and a device in continuous mode is *silent* to Modbus
(and vice-versa), `AUTO`:

1. **Drain** input — and ensure the Transport pushback buffer is empty, so the byte stream
   handed to `anymodbus`'s framer starts clean (single-reader discipline, §5.1.1).
2. **Probe Modbus** at the configured `address` with a cheap **FC08 loopback** (fallback: a
   1-register FC04 read at PDU 0). Short per-try timeout, with `RetryPolicy` retries to absorb a
   dropped frame on the under-spaced bus (§13.5). A valid RTU response → `MODBUS_RTU`; retry the
   same probe with ASCII framing → `MODBUS_ASCII`.
3. Else **passive-listen** for a continuous frame: `read_until(CRLF)` within a window of
   `max(2 × expected_frame_frequency, listen_timeout)`; a checksum-valid parse →
   `CONTINUOUS_ASCII`.
4. Else `ServomexConnectionError` ("no recognised protocol"; context carries what was tried).

Probing Modbus first is cheap (fast req/resp, fails fast when silent); the slow path (waiting
out a possibly-multi-second continuous cadence) is last. All timeouts are configurable on
`open_device`.

---

## 5. Modbus engine — extend `anymodbus`, but keep our `Transport`

**Decision (chosen):** use the in-house `anymodbus` as the Modbus engine (a shared family
asset we own and can fix) **rather than hand-rolling a codec** — but resolve its two structural
mismatches so it fits this device and our hardware-free testing story.

**Status — RESOLVED in `anymodbus 0.2.0` (2026-05-30).** All five §5.1 enhancements (and the
test-slave parity work) shipped in a single `0.2.0` minor; open item §13.4 is closed. The
`[modbus-ascii]` / `AUTO` extra pins `anymodbus>=0.2,<0.3`. The public surface we consume:

```python
from anymodbus import Bus, Framing, RegisterSource, open_modbus_ascii
from anymodbus.lrc import lrc8, lrc8_bytes, verify_lrc   # submodule, mirrors anymodbus.crc

bus   = Bus(our_transport, framing=Framing.ASCII)        # caller owns the port; bus.framing is introspectable
o2    = await bus.slave(30).read_float(0, source=RegisterSource.INPUT)   # FC04 typed helper
alive = await bus.slave(30).diagnostic_loopback(b"\xAB\xCD")            # FC08 sub-0 → echoes b"\xAB\xCD"
```

What 0.1.1 already gave us (unchanged): `anymodbus.Bus` accepts a caller-provided
`stream: anyio.abc.ByteStream` (`bus.py`) — only the `open_modbus_rtu(path)` *convenience
wrapper* opens its own port — so binding Modbus to OUR `Transport`/`FakeTransport` was already
possible, and "transport decoupling" turned out to be sugar only. FC04 raw reads
(`read_input_registers`) and the pure `decode_float32`/`decode_string` codec were already public;
`0.2.0` adds the *typed* FC04 helpers on top via `source=RegisterSource.INPUT`. *(Correction: the
design's earlier `execute()`/`ModbusOp.fn` phrasing was inaccurate — that op layer is
`watlowlib`'s, not `anymodbus`'s; FC04 is a first-class `Slave.read_input_registers` method.)*

### 5.0 Binding strategy — and how it deliberately differs from `watlowlib`

`watlowlib` is the family's other `anymodbus` consumer, but **we bind to it differently, on
purpose** — this is an internal divergence (like `nidaqlib`/`dtollib` dropping `commands/`), not
a break with the boundary harmony of §1.

| | `watlowlib` | `servomexlib` (this design) |
|---|---|---|
| Port ownership | `anymodbus` owns it: `ModbusBusTransport.open()` calls `open_modbus_rtu(path)` | **We own it:** `anymodbus.Bus(stream=our Transport)` (§5.1.1) |
| Why | Watlow is Modbus-only — no need to share a port with another mode | The 4100 has **three mutually-exclusive modes on one port**; the `AUTO` ladder (§4.4) must sniff raw bytes *before* it knows the protocol, so a single uniform `Transport` must back all three |
| Modbus test seam | method-level `FakeSlave`/`StubSlave` (scripts `(method, addr) → words`, bypassing framing/CRC) | **byte-accurate via reused `anymodbus.testing.MockSlave`** (§5.2) — emits real framed ADUs, because AUTO/ASCII/LRC tests exercise the framer |

**Consequence for testing (now smaller than first budgeted):** because we feed `anymodbus`'s
framer from a byte stream, the hardware-free Modbus fake must be a genuine *slave simulator* —
real RTU ADUs (CRC-16), ASCII ADUs (LRC), FC08 sub-0 echo, exception frames — not a method-level
stub. **As of `anymodbus 0.2.0` we do not build this ourselves:** `MockSlave` gained ASCII +
FC08 + bad-LRC-drop, so `servomexlib.testing` just preloads it with the 4100 banks (§5.2). We
*may* additionally keep a cheap watlow-style method-level fake for the pure semantic-op unit
tests; the `MockSlave`-backed byte-accurate path covers AUTO/framing/ASCII and is no longer
servomexlib's to maintain.

### 5.1 Upstream `anymodbus` enhancements — **shipped in `anymodbus 0.2.0`**

These are generically useful and belong in `anymodbus`, not buried in servomexlib. All five
landed in `0.2.0`; each item below records the **as-shipped** interface (the strawman APIs in
the handoff were accepted with the spellings noted):

1. **Transport decoupling — *shipped as a `framing=` kwarg on `Bus`* (was largely already
   present).** `anymodbus.Bus` already bound to a **caller-provided byte stream**
   (`anyio.abc.ByteStream` — which both `anyserial.SerialPort` and our `FakeTransport` satisfy),
   so we use that constructor path directly instead of `open_modbus_rtu(path)`. `0.2.0` adds the
   framing selector this needed once ASCII landed: **`Bus(stream, *, config=None,
   framing=Framing.RTU)`** is the load-bearing "I own the port" path, with `bus.framing`
   introspectable. There's also a thin **`open_modbus_ascii(path, *, baudrate, parity,
   data_bits=8)`** convenience opener (`data_bits=7` for classic 7E1) — but servomexlib uses the
   stream-bound `Bus(stream, framing=…)` form, never the port-owning opener. There is **no**
   `RtuBus`/`AsciiBus` split — one `Bus` type, one kwarg. *(The pure-function fallback —
   PDU+CRC/LRC framed in `protocol/modbus/` — is now moot; the framer lives in `anymodbus`.)*

   **Two design constraints this imposes on our `Transport` seam (§2):**
   - **`Transport`/`FakeTransport` MUST satisfy `anyio.abc.ByteStream`** (`receive`/`send`/
     `aclose`) in addition to our convenience helpers (`read_exact`/`read_until`/`read_available`).
     `anyserial.SerialPort` already *is* a `ByteStream`; `SerialTransport` forwards/extends it and
     `FakeTransport` implements it directly. The convenience helpers are a superset layered on top,
     **not** an alternative interface — `anymodbus` only ever sees the `ByteStream` face.
   - **Single-reader discipline at protocol commit.** The continuous path's `read_until`/pushback
     buffer and `anymodbus`'s framer are *two readers of one stream*; bytes parked in the pushback
     buffer are invisible to the framer (and vice-versa). The `AUTO` ladder (§4.4) **drains and
     leaves the pushback buffer empty before handing the stream to `anymodbus`**, and once a mode
     is committed all reads go through exactly one path (Modbus → framer; continuous → pushback).
     Never mix the two on a live stream.
2. **FC04 input registers as first-class *helpers* — *shipped as a `source=` kwarg*.** The high-
   level `read_float`/`read_int32`/`read_string` helpers gained a **`source: RegisterSource`**
   keyword (`RegisterSource.HOLDING` default = FC03, back-compatible; `RegisterSource.INPUT` =
   FC04). Write helpers are unchanged (input registers are read-only). The 4100 exposes
   measurements as **input registers only**, so our client always passes `source=INPUT`. *(The
   interim path — `read_input_registers` + `decoders.decode_float32`/`decode_string` directly —
   still works and remains the fallback; the typed helper just removes the glue.)*
3. **Modbus-ASCII framing — *shipped; full framer lives in `anymodbus`*.** `:`-prefixed,
   ASCII-hex body, **LRC** checksum, `CRLF` terminator. The complete ASCII framer (not just the
   LRC primitives) is in `anymodbus` and **reuses `pdu.py` verbatim** — we do not frame ASCII
   ourselves. LRC is exposed as pure functions in the **`anymodbus.lrc`** submodule (mirroring
   `anymodbus.crc`): `lrc8`, `lrc8_bytes`, `verify_lrc` *(note: `lrc8_bytes` plural, vs the
   handoff's strawman `lrc8_byte`)*. Selected via `framing=Framing.ASCII` (item 1).
4. **FC08 sub-function 0 loopback — *shipped, scoped to sub-0 only*.** `await
   slave.diagnostic_loopback(b"\xAB\xCD") == b"\xAB\xCD"`. Scoped to sub-function `0x0000` only
   (fixed 6-byte RTU tail), exactly as proposed; other sub-functions stay unframable by
   construction. Used by the `AUTO` probe (§4.4) and `servomex-diag`.
5. **Inter-frame / retry timing knobs — *shipped as `TimingConfig` + `RetryPolicy`*.** The knob
   that actually matters for this device is **`inter_frame_idle`**: the 4100 needs ~50 ms of bus
   silence between transactions or it drops ~25% of rapid sequential reads (§13.5 — *not* the
   one-shot `startup_settle` cold-start settle originally theorised here, which had no measurable
   effect). `ModbusSession` therefore overrides the spec-t3.5 `"auto"` default with a fixed
   `inter_frame_idle=0.05` and `RetryPolicy(retries=2)`. `reset_input_buffer_before_request`
   stays configurable; the shared-port caveat (a flush can discard bytes a continuous-mode reader
   parked — §5.1.1) is documented in `anymodbus`'s `docs/timing.md`.

`servomexlib` pins `anymodbus>=0.2,<0.3` for the `[modbus-ascii]`/`AUTO` extra as an
**optional dependency** so continuous-only users stay lean (RTU + continuous core ships against
`>=0.1.1` via the interim path — §11).

### 5.2 What stays in `servomexlib`

The Modbus engine is generic; the **device knowledge** is ours:
`protocol/modbus/registers.py` (channel↔register arithmetic from the registry, §6),
`protocol/modbus/client.py` (the ~5 semantic ops, charset routing, float/string decode policy),
and `protocol/modbus/errors.py` (anymodbus exception → `ServomexModbusError` mapping). The
`Slave` is bound to **our** `Transport` via the §5.1.1 path.

**Byte-accurate fake — *we reuse `anymodbus.testing.MockSlave`, not a hand-rolled simulator*
(decided by `anymodbus 0.2.0`).** `0.2.0` taught `MockSlave` both ASCII framing and FC08 sub-0
echo, backing one register bank with both an RTU and an ASCII bus:
`client_slave_pair(framing=Framing.ASCII)` / `MockSlave(framing=…)`. It reuses the shared
`read_ascii_frame` / `encode_ascii_adu`, so the wire format is single-sourced between client and
mock, and bad-LRC requests are **dropped** (not crashed), mirroring the RTU bad-CRC drop. So
`servomexlib.testing` does **not** build its own byte-level ADU simulator: it preloads a
`MockSlave` with the 4100's register/coil banks (§6 / Appendix), and that one instance — driven
by both an RTU `Bus` and an ASCII `Bus` over an in-process `ByteStream` — answers FC01/02/04/05/08,
echoes FC08 sub-0, and emits exception frames (01/02). This is what lets the `AUTO` ladder and the
ASCII/LRC framing be tested without hardware. *(A cheap method-level fake may still back the pure
semantic-op unit tests; the `MockSlave`-based byte-accurate path is required for AUTO/framing
coverage and is now upstream, not ours to maintain.)*

---

## 6. The registry — one spine for both protocols

`registry/channels.py` is the heart of the device-fit core. A single declarative table maps
every channel to its addressing in **both** worlds:

```python
@dataclass(frozen=True, slots=True)
class ChannelSpec:
    channel: ChannelId          # I1..I4, D1..D4, E1..E2
    kind: ChannelKind           # TRANSDUCER | DERIVED | EXTERNAL_INPUT
    # Modbus addressing (data-model, 1-based; codec lowers to PDU):
    value_register: int         # e.g. I1 → 30001
    name_register: int          # e.g. I1 → 30003 (3 regs)
    unit_register: int          # e.g. I1 → 30006 (2 regs)
    status_discrete: int        # e.g. I1 → 10001 (stride-8 block base)
    # Continuous mode is self-describing (id/name/value/unit inline), so the parser needs
    # no per-channel address — only `kind` classification from this table.
```

The full table is **generated from the stride pattern** (I-block stride 7 regs from 30001,
discrete stride 8 from 10001; D-block from 30029/10033; E-block from 30057/10065) and
**eagerly validated at import** — a malformed/overlapping entry fails loud as
`ServomexConfigurationError`. The Modbus client reads addresses from here; the continuous
parser reads `kind` from here; `identify()` walks it to report populated slots. One source of
truth, consulted by both protocols — the role sartorius's command table plays, minus the
command machinery.

`registry/units.py` (`Unit`/`UnitKind` enums, `%`/`vpm`/`mA`/…, the 4000-series ×10000
percent↔vpm trace scale, `to_pint`), `registry/status.py` (flag decode from both the Modbus
discrete bitmap and the continuous ASCII status fields → one `ChannelStatus`, including the
per-`kind` bit-layout exceptions for EXTERNAL_INPUT `Invalid` and DERIVED copy-flags — §3.2), and
`registry/charset.py` (§3.3) complete the layer.

---

## 7. Public API

### Entry point (free async function — family convention)

```python
async def open_device(
    port: str | Transport,
    *,
    protocol: ProtocolKind = ProtocolKind.AUTO,
    address: int = 1,                                 # Modbus slave addr (RS485 multidrop)
    serial_settings: SerialSettings | None = None,    # default 19200 8-N-1
    timeout: float = 1.0,
    identify: bool = True,
) -> Analyzer: ...
```

`port` is positional and accepts a `str` **or** a pre-built `Transport` (passing a
`FakeTransport` is the no-hardware path). Everything else is keyword-only. `serial_settings`
defaults to the observed `19200 / 8-N-1`; valid baud 2400–19200 (§7.2.6). When `identify=True`,
the factory caches `DeviceInfo` (and, in continuous mode, waits for the first frame).

### The `Analyzer` facade (async context manager)

```python
async with await open_device("COM11", protocol=ProtocolKind.CONTINUOUS_ASCII) as anz:
    info = await anz.identify()              # DeviceInfo: populated channels, names, units, fw
    o2   = await anz.read_channel("I1")      # Reading(value, unit, status, protocol, raw, ts)
    frame = await anz.poll()                 # Frame — all live channels + analyser status, one tick
    snap  = anz.snapshot()                   # no-I/O cached Frame

    async for sample in anz.stream():        # AUTOPRINT in continuous; POLL in Modbus
        print(sample.channel, sample.value, sample.unit)
```

```python
# Modbus adds the control surface (gated by confirm + SafetyTier):
async with await open_device("COM11", protocol=ProtocolKind.MODBUS_RTU, address=30) as anz:
    await anz.start_calibration(group=1, confirm=True)
    prog = await anz.calibration_status()
    await anz.stop_calibration(confirm=True)
```

**Method naming (harmonised with the family):**

- Reads: `poll() -> Frame` (canonical one-shot, all channels), `read_channel(id) -> Reading`,
  `read_all() -> dict[ChannelId, Reading]`, `status(id) -> ChannelStatus`,
  `analyser_status() -> AnalyserStatus`.
- Identity/state: `identify() -> DeviceInfo`, `snapshot() -> Frame` (no I/O, cached).
- Control (Modbus only): `start_calibration(group, *, confirm=False)`,
  `stop_calibration(*, confirm=False)`, `calibration_status() -> CalibrationProgress`.
- Streaming: `stream(*, mode=None, rate_hz=None) -> StreamingSession` (mode defaults per
  protocol). Drives `record()` for drift-free batched acquisition into `sinks/`.
- **Per-call keyword-only `timeout`** on every I/O method (ruff `ASYNC109` suppressed,
  family-wide).
- **Persistent/disruptive ops require `confirm=True`** (autocalibration is
  `SafetyTier.STATEFUL`).

### Sync facade

```python
from servomexlib.sync import Servomex
with Servomex.open("COM11", protocol="modbus_rtu", address=30) as anz:
    print(anz.poll())
    anz.start_calibration(1, confirm=True)
```

`SyncAnalyzer` wraps `Analyzer` through an `anyio` `BlockingPortal`; every async method has a
one-line sync twin. `SyncManager` wraps `ServomexManager` likewise.

### Manager (RS485 multidrop — Modbus only)

`ServomexManager` registers named analysers across one or more ports and follows the
`watlowlib` concurrency contract:

- analysers sharing one port **serialise** through a shared transport/client lock;
- analysers on **different ports poll concurrently**;
- results use `success`/`failure` wrappers (family `ErrorPolicy`), so one slave's timeout does
  not sink a `poll_all()`;
- continuous-ASCII devices are single-broadcaster, **not** addressable peers — the manager
  refuses to group them as multidrop slaves.

---

## 8. Data models

All **frozen `dataclass(slots=True)`**, `StrEnum` throughout, `py.typed`, clean under
`mypy --strict` + `pyright` strict. No pydantic. Every protocol decodes to the **same** models.

```python
@dataclass(frozen=True, slots=True)
class Reading:
    channel: ChannelId
    kind: ChannelKind
    name: str | None            # "Oxygen"; None when unlabelled (||||||)
    value: float | None         # None on over-range / invalid
    unit: Unit
    status: ChannelStatus
    protocol: ProtocolKind      # which mode produced it
    received_at: datetime       # wall-clock UTC at acquisition
    monotonic_ns: int           # join key for streaming (family §C ts contract)
    raw: bytes                  # the bytes this Reading was decoded from

@dataclass(frozen=True, slots=True)
class ChannelStatus:
    fault: bool; maintenance: bool; calibrating: bool; warming_up: bool
    alarms: tuple[bool, bool, bool, bool]
    @property
    def ok(self) -> bool: ...

@dataclass(frozen=True, slots=True)
class AnalyserStatus:
    fault: bool; maintenance: bool
    cal_groups: tuple[CalGroupState, ...]   # per-group sample/cal + gas-1/gas-2
    clock: datetime | None                  # analyser's own date/time (may be unset)

@dataclass(frozen=True, slots=True)
class Frame:                                 # one continuous frame, or one Modbus sweep
    readings: tuple[Reading, ...]
    analyser: AnalyserStatus
    protocol: ProtocolKind
    received_at: datetime
    monotonic_ns: int
    raw: bytes
    def channel(self, cid: ChannelId) -> Reading: ...
    def as_samples(self) -> list[Sample]: ...

@dataclass(frozen=True, slots=True)
class DeviceInfo:
    model: str                               # "4100D"
    channels: tuple[ChannelInfo, ...]        # id, name, unit, kind per populated slot
    protocol: ProtocolKind
    address: int
    serial_settings: SerialSettings

@dataclass(frozen=True, slots=True)
class CalibrationProgress:
    group: int
    active: bool
    phase: CalPhase                          # IDLE | SAMPLING | CAL_GAS_1 | CAL_GAS_2 | …

@dataclass(frozen=True, slots=True)
class Sample:                                # long-format: one row per channel, for streaming/sinks
    device: str                              # device label (port / manager key)
    channel: ChannelId
    reading: Reading | None                  # None when the row carries only an error
    protocol: ProtocolKind
    monotonic_ns: int                        # join key (family §C ts contract)
    received_at: datetime                    # wall-clock UTC at acquisition
    requested_at: datetime | None = None     # None in passive continuous mode (we didn't ask)
    latency_s: float | None = None           # None in passive mode (no request to measure against)
    metadata: Mapping[str, object] = field(default_factory=dict)
    error: BaseException | None = None       # set when a frame was dropped/corrupt (resync; §4.2)
```

Enums: `ChannelId`, `ChannelKind`, `Unit` (`PERCENT="%"`, `VPM="vpm"`, `MILLIAMP="mA"`, …),
`UnitKind`, `ProtocolKind`, `StreamMode`, `SafetyTier`, `CalPhase`, `Capability` (`Flag`).
**Unit note (§1.7):** the 4000-series prime measurement is **percent**; trace `vpm` carries a
default ×10 000 scale — modelled in `registry/units.py`.

---

## 9. Error hierarchy

One root `ServomexError(Exception)` carrying a frozen `ErrorContext` (port, protocol, address,
channel, register/function code, request/response bytes, elapsed) with `.with_context(**)` and
a context-rendering `__str__` — identical pattern to the siblings (`ErrorContext.merged` +
slot-safe copy in `with_context`).

*Caution on the multiple-inheritance hint below (`ServomexModbusError … also subclass
ProtocolUnsupported where apt`): keep any such cross-branch class single-rooted in MRO terms so
the inherited `__init__(message, *, context)` and the slot-safe `with_context` copy resolve to
one consistent path. Prefer a single base + a marker mixin (no competing `__init__`/`__slots__`)
over genuine diamond inheritance; cover it with a construction/`with_context` round-trip test.*

```
ServomexError
├── ServomexConfigurationError → ServomexValidationError, ServomexConfirmationRequiredError
├── ServomexTransportError     → ServomexTimeoutError, ServomexConnectionError
├── ServomexProtocolError      → ServomexParseError, ServomexChecksumError,
│                                 ServomexFrameError, ServomexProtocolUnsupportedError
├── ServomexModbusError        → ServomexIllegalFunctionError, ServomexIllegalDataAddressError
│                                 (also subclass ProtocolUnsupported where apt)
├── ServomexCapabilityError    → ServomexUnsupportedCommandError
└── ServomexSinkError          → schema / write / dependency
```

---

## 10. Testing strategy

- **No hardware in CI.** `FakeTransport` implements `Transport` in-process from a scripted
  write→reply map; re-exported from the public `servomexlib.testing` seam with fixture loaders.
  Because we own the bytes for *all three* modes (§5), a **byte-accurate** Modbus fake answers
  FC01/02/04/05/08 entirely in-process — the Modbus path, the framer, *and* the `AUTO` ladder
  are fully testable without a device. As of `anymodbus 0.2.0` this fake is a thin
  `servomexlib.testing` wrapper that **preloads `anymodbus.testing.MockSlave`** (which now speaks
  both RTU CRC-16 and ASCII LRC ADUs, echoes FC08 sub-0, drops bad-LRC/bad-CRC frames, and emits
  exception frames 01/02) with the 4100's register/coil banks — we no longer hand-roll ADU
  building (§5.2). *(A cheaper method-level fake may additionally back the pure semantic-op tests,
  as `watlowlib` does; the `MockSlave`-backed byte-accurate path remains required for AUTO/framing
  coverage.)*
- **Recorded fixtures:** the real `COM11` continuous capture
  (`continuous_4100_idle_5ch.bin`, the checksum/parse baseline) and synthesised Modbus
  register/coil tables; golden `Reading`/`Frame`/`Sample`/CSV outputs for regression. Fixtures
  use the human-readable `> hex` / `< hex` arrow format shared with the sibling `.testing`
  modules.
- **Property tests (hypothesis):** IEEE-754 float32 round-trip through the Modbus codec;
  continuous-frame build→parse→checksum round-trip; CRC-16 and LRC round-trips; charset
  decode never raises on arbitrary bytes.
- `conftest.py` parametrises `anyio_backend` across asyncio / asyncio+uvloop / trio (AnyIO
  pytest plugin, **not** pytest-asyncio). `filterwarnings=["error"]`, `xfail_strict`,
  `--strict-markers`, `--import-mode=importlib`.
- **Hardware integration tests** gated by markers (`hardware`, `hardware_stateful`,
  `hardware_destructive`, `slow`), off by default, opt-in via `SERVOMEXLIB_ENABLE_*` env vars.
  Autocalibration tests are `hardware_stateful`. *(Note: on this dev machine, CrowdStrike
  blocks the agent harness from spawning PowerShell for serial probes; live validation runs via
  Bash→Python against `anyserial`/`anymodbus`.)*

---

## 11. Build / tooling

Inherited **verbatim** from the family skeleton (clone `sartoriuslib`, the newest, then rename):

- **Packaging:** hatchling + hatch-vcs dynamic version → `src/servomexlib/_version.py`
  (fallback `0.1.0.dev0`); src-layout; `py.typed`; import name == distribution name.
- **Deps:** core `anyio>=4.13`, `anyserial>=0.1.2,<0.2`; optional `[postgres]`/`[parquet]`/`[docs]`.
  **Modbus extras are staged to avoid hostage-to-upstream (§5.1, §13.4):**
  `servomexlib[modbus]` → `anymodbus>=0.1.1` covers the **RTU + continuous** core (using the
  validated interim `read_input_registers`/`decode_*` path), so the first release does **not**
  block on an unreleased `anymodbus`; `servomexlib[modbus-ascii]` (the `AUTO` ladder, ASCII/LRC
  framing, FC08 diag) pins `anymodbus>=0.2,<0.3` — **now a real cut: `anymodbus 0.2.0` shipped the
  §5.1 enhancements (2026-05-30).** *(Migration when the pin moves to `0.2`: it's additive — the
  interim `read_input_registers`/`decode_*` calls still work; the only change is swapping them for
  `read_float(..., source=RegisterSource.INPUT)` and adopting `Framing.ASCII` / `diagnostic_loopback`
  / `TimingConfig.startup_settle`.)*
- **Quality:** PEP-735 dependency groups (lint/type/test/docs/dev); ruff (line 100, Google
  docstrings, big `select`, `asyncio.wait_for`→`anyio.fail_after` banned-API); mypy strict;
  pyright strict.
- **CI:** `ci.yml` (lint → typecheck → test matrix ubuntu/macos/windows × py3.13/3.14 →
  build), `release.yml` (PyPI Trusted Publishing via OIDC + attestations, manual tag cut,
  hatch-vcs version), `docs.yml` (zensical → GitHub Pages). Plus `dependabot.yml`, issue/PR
  templates, `.pre-commit-config.yaml`, `.editorconfig`, `.gitattributes`, `.python-version`
  (3.13), `CONTRIBUTING`/`SECURITY`/`CHANGELOG` (Keep-a-Changelog), MIT `LICENSE`. Adopt
  dtollib's `fetch-depth: 0` on lint/typecheck jobs (hatch-vcs needs full history).
- **Docs:** zensical.toml (Material-style, mkdocstrings python over `src/`) + `docs/`
  (this design doc + index/installation/quickstart-async/quickstart-sync + `docs/api/`
  per-module stubs).
- **Console scripts** (each `--fixture`-driveable for hardware-free CI):
  `servomex-read`, `servomex-stream`, `servomex-discover`, `servomex-decode` (decode a hex/raw
  continuous frame offline), `servomex-capture` (poll/subscribe → sink), `servomex-diag`
  (loopback, frame tap, jitter).

---

## 12. Implementation outline

Sequenced so each part is independently testable and the first three need **no hardware**
(`FakeTransport` + fixtures, §10):

1. **Scaffold + continuous parser.** Clone the family tooling (§11), rename, wire CI green from
   day one. Define `errors.py`, the enums, and the frozen models (§8). Implement
   `checksum.py` + the pure `parse_frame` (§3.1) against the captured fixture. Ship
   `servomex-decode`. *(Proves package shape with zero hardware.)*
2. **Transport + passive continuous client.** `SerialTransport` over `anyserial`,
   `FakeTransport`, the `ContinuousClient` background receive loop + latest-frame cache + fan-out
   (§4.2), `Analyzer.poll()`/`read_channel()` in continuous mode, passive `stream()`.
3. **Modbus profile.** `registry/channels.py` spine (§6), `protocol/modbus/registers.py`
   address math, the ~5 semantic ops over `anymodbus` bound to our `Transport` (§5),
   charset routing (§3.3), the `MockSlave`-backed `FakeModbusSlave` (§5.2). RTU first; ASCII
   framing behind the same client — now unblocked, since the §5.1 work shipped in `anymodbus
   0.2.0` (`Framing.ASCII`, `source=RegisterSource.INPUT`, `diagnostic_loopback`).
4. **Facade + sync + AUTO.** `open_device`, cached `identify()`/`snapshot()`, the `AUTO` sniff
   ladder (§4.4), the `Servomex.open(...)` sync facade. Examples + quickstart docs.
5. **Safety-gated control.** `start_calibration`/`stop_calibration`/`calibration_status` with the
   `confirm=True` + `SafetyTier.STATEFUL` gates (§4.3), fake coil-pulse tests, opt-in
   `hardware_stateful` tests.
6. **Manager, sinks, CLI, polish.** `ServomexManager` (§7), JSONL/CSV/SQLite sinks (+ optional
   parquet/postgres), the remaining CLIs, expanded docs.

## 13. Open items to resolve before/while coding

1. ~~**Continuous-mode checksum**~~ — **RESOLVED** (§3.1): 16-bit additive over the body after
   the start-space through the pre-checksum `;`, `& 0xFFFF`, 4 hex digits. Confirmed vs 6 live
   frames.
2. ~~**Frame field reconciliation**~~ — **RESOLVED** (§3.1): 5-field header (`date;time;FM;
   autocal(8);N`), each block exactly 8 `;`-fields.
3. ~~**Modbus RTU register map / word order**~~ — **RESOLVED** (§3.2): FC04 input regs,
   IEEE-754 hi-word-first, stride-7 value/name/unit + stride-8 discrete status; validated live
   (addr 30) and cross-checked against the continuous capture.
4. ~~**`anymodbus` enhancements (§5.1)**~~ — **RESOLVED** (`anymodbus 0.2.0`, 2026-05-30):
   transport-decoupling (`Bus(stream, framing=…)`), FC04 typed helpers (`source=RegisterSource.INPUT`),
   ASCII/LRC framing (`Framing.ASCII` + `anymodbus.lrc`), FC08 sub-0 (`diagnostic_loopback`), and
   cold-start timing (`TimingConfig.startup_settle`) all shipped; `[modbus-ascii]`/`AUTO` pins
   `>=0.2,<0.3` (§5, §5.1, §11). The byte-accurate fake question is also settled: **reuse
   `anymodbus.testing.MockSlave`** (now RTU+ASCII+FC08+bad-LRC-drop) preloaded with the 4100 banks,
   plus an optional method-level companion for pure semantic-op tests (§5.2/§10). ~~*Still open:*
   validate the Modbus-ASCII path live.~~ **RESOLVED** (2026-05-30): all three modes validated
   live against the bench 4100D via `tests/hardware/test_hardware_reads.py` — Modbus RTU (17/17),
   **Modbus ASCII** (17/17, `Framing.ASCII`/LRC), continuous broadcast (6/6).
5. ~~**Inter-frame timing**~~ — **RESOLVED** (2026-05-30, *supersedes the earlier "cold-start
   turnaround" theory, which was a misattribution*): the bench 4100/USB-RS485 link drops **~25%
   of Modbus transactions hammered back-to-back** at the RTU-spec t3.5 (~2 ms at 19200, which is
   `anymodbus`'s `inter_frame_idle="auto"` default). Measured drop rate vs the idle gap between
   transactions: 26% @0 ms → 8% @10–20 ms → **0% @50 ms**. It is *not* a cold-start/first-frame
   effect — isolated reads answer in ~20–40 ms even on a freshly-opened idle port; only rapid
   *sequential* reads desync. **Fix:** `ModbusSession` sets `TimingConfig(inter_frame_idle=0.05)`
   + `RetryPolicy(retries=2)`, and the client does **not** wrap a high-level op in an outer
   `fail_after` (a frame sweep is many transactions, so one outer deadline equal to a single
   `request_timeout` cancels legitimate mid-sweep retries — that was a real latent bug).
6. ~~**`AUTO` ladder timeouts (§4.4)**~~ — **RESOLVED** (2026-05-30): AUTO resolves all three
   modes live (FC08 Modbus probe → RTU/ASCII; else passive-listen → continuous). The one knob
   that matters: the continuous **listen window must exceed the broadcast period, which is
   operator-configurable 1–999 s** (front panel), so it cannot be a fixed default — size it from
   the expected frame frequency (the hardware suite takes `SERVOMEXLIB_HARDWARE_BROADCAST_S`).
7. **Channel population across configs** — confirm names/units cache at `identify()` (Modbus
   strings / first continuous frame) and how a varying `N` (3–7 channels, presence of D-block /
   I4) is reported. Observed here: `N=05` → I1/I2/I3/E1/E2. Note `identify()` treats a `||||||`
   name (E1/E2 idle) as an **unpopulated** slot, so it is omitted from `DeviceInfo.channels`.
   **Modbus read coalescing implemented (2026-05-30):** `identify()` refreshes the static
   name/unit cache and narrows full-frame sweeps to named populated slots; `read_channel()` can
   still read an omitted slot directly. A first poll without `identify()` populates missing
   metadata from the coalesced input-register block so the default API remains transparent.
8. **Control surface scope** — v1 ships reads + status + autocalibration start/stop/status (the
   whole Modbus profile is read + autocal; richer config is front-panel only). Confirm no other
   coils/registers need exposing.

---

## 14. Source documents & references

The manuals are kept locally under `docs/manuals/` but **git-ignored** (large, vendor-
proprietary — not redistributed). Clone them into place to follow the section references above.

- **`docs/manuals/servomex_4100_manual.md`** — SERVOPRO 4000-series **Installation Manual**;
  the primary protocol authority. Cited: §3.3 serial/Modbus connection (PL6, RS232/RS485),
  §3.4 Continuous mode (Tables 3.6/3.7), §3.5 Modbus mode, §7.2.6 serial specs (2400–19200
  baud), §1.4–1.7 transducer/output numbering & unit conversion, §4.5 Autocalibration, and
  **Appendix B Modbus Profile** (register/coil/discrete-input map).
- **`docs/manuals/servomex_4100_service_manual.md`** — **Service Manual**; §1.6–1.7 cover the
  front-panel UI (selecting comm mode, frame-frequency, Modbus slave address, serial params)
  and calibration/diagnostic procedures.
- **`tests/fixtures/captures/continuous_4100_idle_5ch.bin`** — 6 live continuous frames off
  `COM11` (checksum + parser regression baseline; §3.1).
- **`scripts/probe_modbus.py`, `scripts/probe_name_bytes.py`** — read-only live Modbus-RTU
  probes (run via `anymodbus`+`anyserial`) used to validate Appendix B (§3.2) and characterise
  the display-charset names (§3.3).

**Sibling libraries** (`c:\Users\gbellamy\Documents\git\`) — what each contributes as a reference:

- **`sartoriuslib`** — newest multi-protocol template; source the **outer shell + tooling
  skeleton verbatim** (entry point, models style, error hierarchy, streaming/sinks/sync/CLI, CI).
  Its `commands/`+`Command`/Variant core is **deliberately not adopted** (§1–§2).
- **`watlowlib`** — the Modbus sibling; reference for register-table organisation, per-call
  `address` multidrop, and the manager concurrency contract (§7). Uses the same `anymodbus`.
- **`alicatlib`** — serial-ASCII reference for transport injection, fake/captured-frame tests,
  and identify/probe-before-facade behaviour.
- **`nidaqlib` / `dtollib`** — confirm the family norm that the **internal** model is device-fit
  (both use a `TaskSpec`, not Command/Variant) — the precedent this design follows by dropping
  `commands/`.
- **`anyserial`** — raw-bytes async transport foundation; framing/Modbus codecs stay in
  `servomexlib`/`anymodbus`, never pushed down into it.
