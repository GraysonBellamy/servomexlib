"""``servomex-discover`` — probe one or more ports and report what answers.

Runs the read-only ``AUTO`` ladder + one ``identify`` per port,
printing the resolved protocol and populated channels. Exit code ``0`` if any
device was recognised, ``2`` if none.

Examples::

    servomex-discover COM11 COM12
    servomex-discover --fixture capture.bin
"""

from __future__ import annotations

import argparse
import sys
from typing import TYPE_CHECKING

from servomexlib.cli._common import run_async_cli
from servomexlib.devices.discovery import discover_port, summarize

if TYPE_CHECKING:
    from collections.abc import Sequence

    from servomexlib.devices.discovery import DiscoveryResult

__all__ = ["main"]


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point — returns the process exit code (0 found, 2 none)."""
    parser = argparse.ArgumentParser(
        prog="servomex-discover", description="Probe ports for Servomex analysers."
    )
    parser.add_argument("ports", nargs="*", help="Serial ports to probe (e.g. COM11 COM12).")
    parser.add_argument(
        "--address", type=int, default=1, help="Modbus slave address to probe (default 1)."
    )
    parser.add_argument(
        "--timeout", type=float, default=1.0, help="Per-port probe timeout (default 1.0)."
    )
    parser.add_argument(
        "--fixture",
        metavar="CAPTURE",
        default=None,
        help="A continuous capture file to probe a FakeTransport (no hardware).",
    )
    args = parser.parse_args(argv)
    return run_async_cli(lambda: _run(args))


async def _run(args: argparse.Namespace) -> int:
    results: list[DiscoveryResult] = []
    if args.fixture is not None:
        results.append(await _probe_fixture(args.fixture, args.timeout))
    results.extend(
        [
            await discover_port(port, address=args.address, timeout=args.timeout)
            for port in args.ports
        ]
    )

    if not results:
        sys.stderr.write("error: give one or more ports, or --fixture\n")
        return 2

    for result in results:
        _print_result(result)
    summary = summarize(results)
    sys.stdout.write(f"\n{len(summary.found)}/{summary.total} port(s) recognised\n")
    return 0 if summary.found else 2


async def _probe_fixture(fixture: str, timeout: float) -> DiscoveryResult:
    from pathlib import Path  # noqa: PLC0415

    import anyio  # noqa: PLC0415

    from servomexlib.testing import FakeTransport, split_continuous_frames  # noqa: PLC0415

    capture = Path(fixture).read_bytes()
    frames = [frame + b"\r\n" for frame in split_continuous_frames(capture)]
    fake = FakeTransport()
    for frame in frames:
        fake.feed(frame)

    async def _emit() -> None:
        # Keep re-feeding so the AUTO listen window always sees a frame.
        while True:
            for frame in frames:
                fake.feed(frame)
            await anyio.sleep(0.05)

    async with anyio.create_task_group() as tg:
        _ = tg.start_soon(_emit)
        try:
            return await discover_port(fake, timeout=timeout)
        finally:
            tg.cancel()
    # Unreachable: the task group propagates the result/exception above. Spelled
    # out so the checkers see a return/raise on every path past the task group.
    raise AssertionError("unreachable")  # pragma: no cover


def _print_result(result: DiscoveryResult) -> None:
    if not result.ok:
        sys.stdout.write(f"{result.port}: no device ({result.error})\n")
        return
    protocol = result.protocol.value if result.protocol is not None else "?"
    broadcaster = " [broadcaster]" if result.is_broadcaster else ""
    sys.stdout.write(f"{result.port}: {protocol}{broadcaster}\n")
    if result.info is not None:
        for ch in result.info.channels:
            sys.stdout.write(f"    {ch.channel.value:<3} {ch.name} ({ch.unit.value})\n")
