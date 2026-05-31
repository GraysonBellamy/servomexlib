# servomexlib

> Async-first Python driver for **Servomex SERVOPRO 4000-series gas analysers**
> (developed against a bench **4100D**), presenting one protocol-neutral,
> channel-oriented API across the analyser's three communication modes.

`servomexlib` decodes **Continuous ASCII** (unsolicited broadcast),
**Modbus RTU**, and **Modbus ASCII** into a single set of typed, frozen models.
`poll()`, `read_channel("I1")`, `snapshot()`, `identify()`, `stream()`, and
`start_calibration(group)` behave identically regardless of the wire mode.

- **Async core** on [`anyio`](https://anyio.readthedocs.io); a thin sync facade
  (`Servomex.open(...)`) for scripts and REPL use.
- **No hardware needed** to develop or test — a `FakeTransport` + recorded
  fixtures drive every protocol and the CLI in CI.
- A member of the `*lib` instrument-driver family; harmony is defined at the
  boundary (entry point, models, errors, tooling).

## Status

Early development. See [`docs/design.md`](docs/design.md) for the architecture
and [`CHANGELOG.md`](CHANGELOG.md) for what has shipped so far.

## Installation

```bash
pip install servomexlib                 # continuous-ASCII core
pip install "servomexlib[modbus]"       # + Modbus RTU
pip install "servomexlib[modbus-ascii]" # + Modbus ASCII, AUTO sniffing, diagnostics
```

## Quickstart

```python
import anyio
from servomexlib import open_device, ProtocolKind


async def main() -> None:
    async with await open_device("COM11", protocol=ProtocolKind.CONTINUOUS_ASCII) as anz:
        frame = await anz.poll()
        for reading in frame.readings:
            print(reading.channel, reading.value, reading.unit)

        # Stream samples (AUTOPRINT in continuous; POLL at rate_hz in Modbus):
        async with anz.stream() as samples:
            async for sample in samples:
                print(sample.channel, sample.reading.value if sample.reading else None)


anyio.run(main)
```

Modbus adds the safety-gated control surface:

```python
async with await open_device("COM11", protocol=ProtocolKind.MODBUS_RTU, address=1) as anz:
    await anz.start_calibration(group=1, confirm=True)   # STATEFUL → confirm required
    print(await anz.calibration_status(1))
    await anz.stop_calibration(confirm=True)
```

Record to a sink (CSV / JSONL / SQLite, or optional Parquet / Postgres):

```python
from servomexlib.sinks import SqliteSink, pipe
from servomexlib.streaming import record

async with await open_device("COM11", protocol=ProtocolKind.MODBUS_RTU) as anz:
    async with SqliteSink("run.sqlite") as sink, \
            record(anz, rate_hz=2, duration=60) as recording:
        summary = await pipe(recording.stream, sink)
    print(summary.samples_emitted, "samples")
```

A thin sync facade mirrors all of it for scripts/REPL:

```python
from servomexlib.sync import Servomex

with Servomex.open("COM11", protocol="modbus_rtu", address=1) as anz:
    print(anz.poll())
```

## Command-line tools

Each is `--fixture`-driveable for hardware-free use:

| Command | Purpose |
|---|---|
| `servomex-read` | Open, identify, print one frame |
| `servomex-stream` | Print samples as they arrive (or polled) |
| `servomex-discover` | Probe ports and report the protocol found |
| `servomex-decode` | Decode a continuous frame offline (hex / file / stdin) |
| `servomex-capture` | Record to a sink at a fixed cadence |
| `servomex-diag` | Loopback / tap / jitter diagnostics |

See [`examples/capture_to_sqlite.py`](examples/capture_to_sqlite.py) for an
end-to-end acquisition script.

## License

MIT — see [LICENSE](LICENSE).
