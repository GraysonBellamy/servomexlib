"""Shared helpers for the ``servomex-*`` CLIs.

:func:`run_cli` / :func:`run_async_cli` map a :class:`ServomexError` to a clean
exit-1 (no traceback); :func:`hex_tokens_to_bytes` parses hex input;
:func:`add_open_args` registers the shared ``open_device`` argument surface and
:func:`open_analyzer` resolves it into an opened :class:`Analyzer` — either over a
real port or, with ``--fixture``, over a pre-fed :class:`FakeTransport` for
hardware-free CI.
"""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, cast

import anyio

from servomexlib.errors import ServomexError, ServomexValidationError
from servomexlib.protocol.base import ProtocolKind

if TYPE_CHECKING:
    import argparse
    from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence

    from servomexlib.devices.analyzer import Analyzer
    from servomexlib.testing import FakeTransport

__all__ = [
    "add_open_args",
    "hex_tokens_to_bytes",
    "open_analyzer",
    "run_async_cli",
    "run_cli",
]


def hex_tokens_to_bytes(tokens: Sequence[str] | str) -> bytes:
    """Concatenate hex token(s) into bytes, tolerating whitespace and ``:``/``,``.

    Args:
        tokens: A single hex string or a sequence of hex tokens.

    Returns:
        The decoded bytes.

    Raises:
        ValueError: The input is empty, odd-length, or not valid hex.
    """
    joined = tokens if isinstance(tokens, str) else "".join(tokens)
    cleaned = joined.replace(" ", "").replace(":", "").replace(",", "")
    if cleaned == "":
        raise ValueError("empty hex input")
    if len(cleaned) % 2 != 0:
        raise ValueError(f"hex length must be even, got {len(cleaned)} chars: {cleaned!r}")
    try:
        return bytes.fromhex(cleaned)
    except ValueError as exc:
        raise ValueError(f"invalid hex digits: {exc}") from exc


def run_cli(coro_factory: Callable[[], int]) -> int:
    """Run a sync CLI body, mapping :class:`ServomexError` to a clean exit code."""
    try:
        return coro_factory()
    except ServomexError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 1


def run_async_cli(main: Callable[[], Awaitable[int]]) -> int:
    """Run an async CLI body via :func:`anyio.run`, mapping errors to exit-1.

    A :class:`ServomexError` (raised directly or wrapped in an
    :class:`ExceptionGroup` by structured concurrency) is written to stderr and
    becomes exit code ``1``; everything else propagates.
    """
    try:
        return anyio.run(main)
    except ServomexError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 1
    except BaseExceptionGroup as eg:
        servomex = eg.subgroup(ServomexError)
        if servomex is None:
            raise
        sys.stderr.write(f"error: {_first_leaf(servomex)}\n")
        return 1


def _as_group(exc: BaseException) -> BaseExceptionGroup[BaseException] | None:
    """Return ``exc`` as a typed group, or ``None`` if it isn't one.

    ``isinstance`` against the unsubscripted generic loses the type parameter
    (yielding ``BaseExceptionGroup[Unknown]``); the cast re-pins it so callers
    stay fully typed.
    """
    if isinstance(exc, BaseExceptionGroup):
        return cast("BaseExceptionGroup[BaseException]", exc)
    return None


def _first_leaf(eg: BaseExceptionGroup[BaseException]) -> BaseException:
    """Return the first leaf exception of a (possibly nested) group."""
    current: BaseException = eg
    while (group := _as_group(current)) is not None:
        current = group.exceptions[0]
    return current


def add_open_args(parser: argparse.ArgumentParser) -> None:
    """Register the shared ``open_device`` surface on ``parser``.

    Adds ``port`` (optional positional), ``--protocol``, ``--address``, ``--baud``,
    ``--timeout``, and ``--fixture``. With ``--fixture`` the ``port`` is omitted and
    a recorded continuous capture drives a hardware-free :class:`FakeTransport`.
    """
    parser.add_argument(
        "port",
        nargs="?",
        help="Serial port (e.g. COM11). Optional when --fixture is given.",
    )
    parser.add_argument(
        "--protocol",
        default=ProtocolKind.AUTO.value,
        choices=[k.value for k in ProtocolKind],
        help="Wire protocol, or 'auto' to sniff (default: auto).",
    )
    parser.add_argument("--address", type=int, default=1, help="Modbus slave address (default 1).")
    parser.add_argument("--baud", type=int, default=None, help="Serial baud (default 19200).")
    parser.add_argument(
        "--timeout", type=float, default=1.0, help="Per-call timeout seconds (default 1.0)."
    )
    parser.add_argument(
        "--fixture",
        metavar="CAPTURE",
        default=None,
        help="A recorded continuous capture file to drive a FakeTransport (no hardware).",
    )


#: Cadence of the fixture broadcaster — fast enough to keep --count tests snappy,
#: slow enough to bound the inbound buffer and give jitter a measurable gap.
_FIXTURE_BROADCAST_INTERVAL = 0.005


async def _replay_fixture(fake: FakeTransport, frames: Sequence[bytes]) -> None:
    """Feed ``frames`` into ``fake`` on a loop to emulate a live broadcaster.

    Runs until cancelled (when the :func:`open_analyzer` context exits). A capture
    is a finite slice; looping it lets ``--count``-style consumers read as many
    frames as they ask for without the stream ever drying up.
    """
    while True:
        await fake.emit(frames, interval=_FIXTURE_BROADCAST_INTERVAL)


@asynccontextmanager
async def open_analyzer(args: argparse.Namespace) -> AsyncGenerator[Analyzer]:
    """Open an :class:`Analyzer` from parsed ``args`` as an async context manager.

    With ``--fixture`` the recorded continuous frames are fed into an in-process
    :class:`FakeTransport` (continuous mode, no hardware). Otherwise opens the real
    ``port`` with the requested protocol/address/baud.

    Raises:
        ServomexError: ``--fixture`` and ``port`` are both missing, or the open
            fails.
    """
    from servomexlib.devices.factory import open_continuous, open_device  # noqa: PLC0415

    if args.fixture is not None:
        from servomexlib.testing import FakeTransport, split_continuous_frames  # noqa: PLC0415

        capture = Path(args.fixture).read_bytes()
        frames = [frame + b"\r\n" for frame in split_continuous_frames(capture)]
        fake = FakeTransport()
        async with anyio.create_task_group() as tg, await open_continuous(fake) as anz:
            # A capture holds a finite slice of frames, but consumers (read's
            # identify+poll, stream/tap/jitter's --count) expect a live broadcaster.
            # Replay the slice on a loop in the background so the stream never dries
            # up; the task is cancelled when the caller exits the context.
            _ = tg.start_soon(_replay_fixture, fake, frames)
            try:
                yield anz
            finally:
                tg.cancel()
        return

    if args.port is None:
        raise ServomexValidationError("a port is required unless --fixture is given")

    serial_settings = None
    if args.baud is not None:
        from servomexlib.transport.base import SerialSettings  # noqa: PLC0415

        serial_settings = SerialSettings(port=args.port, baudrate=args.baud)
    async with await open_device(
        args.port,
        protocol=ProtocolKind(args.protocol),
        address=args.address,
        serial_settings=serial_settings,
        timeout=args.timeout,
    ) as anz:
        yield anz
