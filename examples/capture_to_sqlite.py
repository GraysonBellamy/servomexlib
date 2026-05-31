"""Record a Servomex analyser to SQLite for a fixed duration.

Run against a real port::

    uv run python examples/capture_to_sqlite.py COM11 --protocol modbus_rtu --address 1

Or hardware-free against the recorded continuous capture fixture::

    uv run python examples/capture_to_sqlite.py --fixture \
        tests/fixtures/captures/continuous_4100_idle_5ch.bin

It opens the analyser, drives the drift-free recorder at a fixed rate into a
SQLite sink, and prints the acquisition summary — the same pipeline the
``servomex-capture`` CLI wraps.
"""

from __future__ import annotations

import argparse
import sys

import anyio
from anyio import Path

from servomexlib.devices.factory import open_continuous, open_device
from servomexlib.protocol.base import ProtocolKind
from servomexlib.sinks import SqliteSink, pipe
from servomexlib.streaming import record
from servomexlib.testing import FakeTransport, split_continuous_frames


async def _amain(args: argparse.Namespace) -> int:
    if args.fixture is not None:
        fixture = await Path(args.fixture).read_bytes()
        frames = [f + b"\r\n" for f in split_continuous_frames(fixture)]
        fake = FakeTransport()
        opener = open_continuous(fake)
    else:
        opener = open_device(args.port, protocol=ProtocolKind(args.protocol), address=args.address)

    sink = SqliteSink(args.out, table="samples")
    async with await opener as anz, sink:
        if args.fixture is not None:
            for frame in frames:
                fake.feed(frame)
        async with record(anz, rate_hz=args.rate, duration=args.duration) as recording:
            summary = await pipe(recording.stream, sink)

    sys.stdout.write(f"captured {summary.samples_emitted} samples to {args.out}\n")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Record a Servomex analyser to SQLite.")
    parser.add_argument("port", nargs="?", help="Serial port (omit with --fixture).")
    parser.add_argument("--protocol", default="modbus_rtu")
    parser.add_argument("--address", type=int, default=1)
    parser.add_argument("--fixture", default=None, help="Continuous capture file (no hardware).")
    parser.add_argument("--out", default="servomex.sqlite")
    parser.add_argument("--rate", type=float, default=2.0)
    parser.add_argument("--duration", type=float, default=5.0)
    return anyio.run(_amain, parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
