"""``servomex-diag`` — diagnostic subcommands (loopback, tap, jitter).

A thin argparse dispatcher over the read-only diagnostics. Each subcommand lives in
its own module and exposes an ``async run(args) -> int``. Destructive subcommands
(none yet) would gate through :mod:`servomexlib.cli.diagnostics._gate`.

Examples::

    servomex-diag loopback COM11 --address 1 --payload "ab cd"
    servomex-diag tap --fixture capture.bin --count 5
    servomex-diag jitter --fixture capture.bin --count 6
"""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

from servomexlib.cli._common import add_open_args, run_async_cli
from servomexlib.cli.diagnostics import jitter, loopback, tap

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["main"]


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point — dispatches to a diagnostic subcommand."""
    parser = argparse.ArgumentParser(prog="servomex-diag", description="Servomex link diagnostics.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_loopback = sub.add_parser("loopback", help="FC08 sub-0 echo probe (Modbus).")
    add_open_args(p_loopback)
    p_loopback.add_argument("--payload", default=None, help="Hex payload to echo (default ABCD).")

    p_tap = sub.add_parser("tap", help="Passively print continuous frames.")
    add_open_args(p_tap)
    p_tap.add_argument("--count", type=int, default=10, help="Stop after N frames (default 10).")

    p_jitter = sub.add_parser("jitter", help="Measure inter-sample cadence.")
    add_open_args(p_jitter)
    p_jitter.add_argument("--rate", type=float, default=1.0, help="Modbus poll rate Hz.")
    p_jitter.add_argument("--count", type=int, default=10, help="Samples to measure (default 10).")

    args = parser.parse_args(argv)
    runner = {"loopback": loopback.run, "tap": tap.run, "jitter": jitter.run}[args.command]
    return run_async_cli(lambda: runner(args))
