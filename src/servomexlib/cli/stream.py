"""``servomex-stream`` — subscribe to (or poll) an analyser and print samples.

Continuous mode prints unsolicited samples as they arrive; Modbus mode polls at
``--rate``. Stops after ``--count`` samples (default 10) or end of stream.

Examples::

    servomex-stream --fixture capture.bin --count 5
    servomex-stream COM11 --protocol modbus_rtu --rate 2 --count 20
"""

from __future__ import annotations

import argparse
import sys
from typing import TYPE_CHECKING

from servomexlib.cli._common import add_open_args, open_analyzer, run_async_cli

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["main"]


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point — returns the process exit code."""
    parser = argparse.ArgumentParser(
        prog="servomex-stream", description="Stream samples from an analyser."
    )
    add_open_args(parser)
    parser.add_argument("--rate", type=float, default=1.0, help="Modbus poll rate Hz (default 1).")
    parser.add_argument("--count", type=int, default=10, help="Stop after N samples (default 10).")
    args = parser.parse_args(argv)
    return run_async_cli(lambda: _run(args))


async def _run(args: argparse.Namespace) -> int:
    written = 0
    async with open_analyzer(args) as anz, anz.stream(rate_hz=args.rate) as samples:
        async for sample in samples:
            if sample.reading is not None:
                value = "None" if sample.reading.value is None else f"{sample.reading.value:g}"
                channel = sample.channel.value if sample.channel is not None else "?"
                sys.stdout.write(f"{channel:<3} {value:>10} {sample.reading.unit.value}\n")
            elif sample.error is not None:
                sys.stdout.write(f"error: {sample.error}\n")
            written += 1
            if written >= args.count:
                break
    return 0
