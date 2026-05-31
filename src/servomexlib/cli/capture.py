"""``servomex-capture`` — record samples to a sink at a fixed cadence.

Drives the drift-free :func:`~servomexlib.streaming.record` loop into a sink chosen
by the ``--out`` extension (``.csv`` / ``.jsonl`` / ``.db`` / ``.sqlite`` /
``.parquet``) or an explicit ``--format``. Prints an acquisition summary on exit.

Examples::

    servomex-capture --fixture capture.bin --out run.csv --rate 5 --duration 2
    servomex-capture COM11 --protocol modbus_rtu --out run.sqlite --rate 2 --duration 60
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from servomexlib.cli._common import add_open_args, open_analyzer, run_async_cli
from servomexlib.sinks import CsvSink, JsonlSink, SqliteSink, pipe
from servomexlib.streaming import record

if TYPE_CHECKING:
    from collections.abc import Sequence

    from servomexlib.sinks.base import SampleSink

__all__ = ["main"]

_EXT_FORMATS = {
    ".csv": "csv",
    ".jsonl": "jsonl",
    ".db": "sqlite",
    ".sqlite": "sqlite",
    ".parquet": "parquet",
}


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point — returns the process exit code."""
    parser = argparse.ArgumentParser(
        prog="servomex-capture", description="Record analyser samples to a sink."
    )
    add_open_args(parser)
    parser.add_argument("--out", required=True, help="Output file; sink chosen by extension.")
    parser.add_argument(
        "--format",
        choices=["csv", "jsonl", "sqlite", "parquet"],
        default=None,
        help="Override the sink format (default: infer from --out extension).",
    )
    parser.add_argument("--rate", type=float, default=1.0, help="Poll rate Hz (default 1).")
    parser.add_argument(
        "--duration", type=float, default=5.0, help="Acquisition seconds (default 5)."
    )
    args = parser.parse_args(argv)
    return run_async_cli(lambda: _run(args))


async def _run(args: argparse.Namespace) -> int:
    sink = _make_sink(args.out, args.format)
    async with (
        open_analyzer(args) as anz,
        sink,
        record(anz, rate_hz=args.rate, duration=args.duration) as recording,
    ):
        summary = await pipe(recording.stream, sink)
    sys.stdout.write(
        f"captured {summary.samples_emitted} samples to {args.out} "
        f"in {(summary.finished_at - summary.started_at).total_seconds():.2f}s\n"
        if summary.finished_at is not None
        else f"captured {summary.samples_emitted} samples to {args.out}\n"
    )
    return 0


def _make_sink(out: str, fmt: str | None) -> SampleSink:
    resolved = fmt or _EXT_FORMATS.get(Path(out).suffix.lower())
    if resolved == "csv":
        return CsvSink(out)
    if resolved == "jsonl":
        return JsonlSink(out)
    if resolved == "sqlite":
        return SqliteSink(out)
    if resolved == "parquet":
        from servomexlib.sinks import ParquetSink  # noqa: PLC0415

        return ParquetSink(out)
    raise SystemExit(
        f"cannot infer sink format from {out!r}; pass --format "
        "(csv/jsonl/sqlite/parquet) or use a known extension"
    )
