"""``servomex-read`` — open an analyser, poll one frame, print it.

Opens the device (real port, or a ``--fixture`` continuous capture for
hardware-free CI), optionally identifies it, then prints one frame's channels in a
human-readable table.

Examples::

    servomex-read COM11 --protocol modbus_rtu --address 1
    servomex-read --fixture tests/fixtures/captures/continuous_4100_idle_5ch.bin
"""

from __future__ import annotations

import argparse
import sys
from typing import TYPE_CHECKING

from servomexlib.cli._common import add_open_args, open_analyzer, run_async_cli

if TYPE_CHECKING:
    from collections.abc import Sequence

    from servomexlib.devices.models import Frame

__all__ = ["main"]


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point — returns the process exit code."""
    parser = argparse.ArgumentParser(
        prog="servomex-read", description="Open an analyser and print one frame."
    )
    add_open_args(parser)
    parser.add_argument(
        "--no-identify", action="store_true", help="Skip the identify step before polling."
    )
    args = parser.parse_args(argv)
    return run_async_cli(lambda: _run(args))


async def _run(args: argparse.Namespace) -> int:
    async with open_analyzer(args) as anz:
        if not args.no_identify:
            info = await anz.identify()
            sys.stdout.write(f"device: {info.model} protocol={info.protocol.value}\n")
        frame = await anz.poll()
    sys.stdout.write(_format_frame(frame))
    return 0


def _format_frame(frame: Frame) -> str:
    lines = [
        f"frame @ {frame.received_at.isoformat()} protocol={frame.protocol.value} "
        f"fault={frame.analyser.fault} maintenance={frame.analyser.maintenance}",
    ]
    for reading in frame.readings:
        name = reading.name if reading.name is not None else "(unlabelled)"
        value = "None" if reading.value is None else f"{reading.value:g}"
        lines.append(
            f"  {reading.channel.value:<3} {name:<12} {value:>10} "
            f"{reading.unit.value:<4} ok={reading.status.ok}"
        )
    return "\n".join(lines) + "\n"
