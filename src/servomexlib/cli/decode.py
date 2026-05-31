"""``servomex-decode`` — decode a continuous-ASCII frame offline (no hardware).

Reads frame bytes from ``--hex``, a ``--fixture`` capture file, or stdin,
recomputes the checksum, and prints a structured per-channel report. A capture
with multiple ``CR LF``-separated frames is decoded frame by frame. A bad
checksum is reported but still decoded as far as the structure allows.

Examples::

    servomex-decode --fixture tests/fixtures/captures/continuous_4100_idle_5ch.bin
    servomex-decode --hex "20 30 36 2d ..."
    cat capture.bin | servomex-decode
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from servomexlib.cli._common import hex_tokens_to_bytes, run_cli
from servomexlib.errors import ServomexChecksumError, ServomexError
from servomexlib.protocol.continuous import checksum
from servomexlib.protocol.continuous.parser import parse_frame

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["decode_capture", "main"]


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point — returns the process exit code (``0`` ok, ``2`` bad args)."""
    parser = _build_argparser()
    args = parser.parse_args(argv)
    return run_cli(lambda: _run(args))


def _run(args: argparse.Namespace) -> int:
    if args.hex is not None:
        try:
            data = hex_tokens_to_bytes(args.hex)
        except ValueError as exc:
            sys.stderr.write(f"error: --hex: {exc}\n")
            return 2
    elif args.fixture is not None:
        data = Path(args.fixture).read_bytes()
    else:
        data = sys.stdin.buffer.read()
    sys.stdout.write(decode_capture(data))
    return 0


def decode_capture(data: bytes) -> str:
    """Decode one or more ``CR LF``-separated frames into a text report.

    Tolerates a bare ``LF`` as well as ``CR LF`` (a capture may have lost its
    ``CR`` to a text-mode transform); the parser strips a trailing ``CR`` anyway.
    """
    frames = [frame.rstrip(b"\r") for frame in data.split(b"\n") if frame.strip(b" \r")]
    if not frames:
        return "no frames found\n"
    multi = len(frames) > 1
    reports = [_decode_frame(frame, index=i if multi else None) for i, frame in enumerate(frames)]
    return "\n".join(reports)


def _decode_frame(content: bytes, *, index: int | None) -> str:
    label = f"frame {index}:" if index is not None else "frame:"
    lines: list[str] = [f"{label} {content!r}", f"  checksum: {_checksum_status(content)}"]
    try:
        frame = parse_frame(content)
    except ServomexError as exc:
        lines.append(f"  parse error: {exc}")
        return _join(lines)

    analyser = frame.analyser
    clock = analyser.clock.isoformat() if analyser.clock is not None else "unset"
    lines.append(f"  protocol: {frame.protocol.value}")
    lines.append(
        f"  analyser: clock={clock} fault={analyser.fault} maintenance={analyser.maintenance}",
    )
    lines.append(f"  channels ({len(frame.readings)}):")
    for reading in frame.readings:
        name = reading.name if reading.name is not None else "(unlabelled)"
        value = "None" if reading.value is None else f"{reading.value:g}"
        lines.append(
            f"    {reading.channel.value:<3} {name:<12} {value:>10} "
            f"{reading.unit.value:<4} ok={reading.status.ok}",
        )
    return _join(lines)


def _checksum_status(content: bytes) -> str:
    try:
        value = checksum.verify(content)
    except ServomexChecksumError as exc:
        return f"INVALID ({exc.args[0] if exc.args else exc})"
    return f"valid (0x{value:04X})"


def _build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="servomex-decode",
        description="Decode a continuous-ASCII frame from hex, a capture file, or stdin.",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--hex",
        nargs="+",
        metavar="HEX",
        help="Frame as space-separated hex bytes (concatenated tokens tolerated).",
    )
    group.add_argument(
        "--fixture",
        metavar="PATH",
        help="Path to a captured continuous-frame file (one or more frames).",
    )
    return parser


def _join(lines: list[str]) -> str:
    return "\n".join(lines) + "\n"
