"""Synchronous sink wrappers.

:class:`SyncSink` adapts any async :class:`~servomexlib.sinks.base.SampleSink` to a
blocking ``open`` / ``write_many`` / ``close`` + context-manager surface, marshalled
through a :class:`~servomexlib.sync.portal.SyncPortal`. Convenience constructors
(:func:`sync_csv_sink`, etc.) wrap the in-tree sinks.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Self

from servomexlib.sync.portal import SyncPortal

if TYPE_CHECKING:
    from collections.abc import Sequence
    from types import TracebackType

    from servomexlib.sinks.base import SampleSink
    from servomexlib.streaming.sample import Sample

__all__ = [
    "SyncSink",
    "sync_csv_sink",
    "sync_jsonl_sink",
    "sync_sqlite_sink",
]


class SyncSink:
    """Blocking adapter over an async :class:`SampleSink`."""

    def __init__(self, sink: SampleSink, *, portal: SyncPortal | None = None) -> None:
        self._sink = sink
        self._owns_portal = portal is None
        self._portal = portal if portal is not None else SyncPortal()

    def __enter__(self) -> Self:
        if self._owns_portal:
            self._portal.__enter__()
        self._portal.call(self._sink.open)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            self._portal.call(self._sink.close)
        finally:
            if self._owns_portal:
                self._portal.__exit__(exc_type, exc, tb)

    def open(self) -> None:
        """Open the backing sink."""
        self._portal.call(self._sink.open)

    def write_many(self, samples: Sequence[Sample]) -> None:
        """Append ``samples`` to the backing sink."""
        self._portal.call(self._sink.write_many, samples)

    def close(self) -> None:
        """Close the backing sink."""
        self._portal.call(self._sink.close)


def sync_csv_sink(path: str | Path, *, portal: SyncPortal | None = None) -> SyncSink:
    """A blocking CSV sink."""
    from servomexlib.sinks import CsvSink  # noqa: PLC0415

    return SyncSink(CsvSink(Path(path)), portal=portal)


def sync_jsonl_sink(path: str | Path, *, portal: SyncPortal | None = None) -> SyncSink:
    """A blocking JSONL sink."""
    from servomexlib.sinks import JsonlSink  # noqa: PLC0415

    return SyncSink(JsonlSink(Path(path)), portal=portal)


def sync_sqlite_sink(
    path: str | Path, *, table: str = "samples", portal: SyncPortal | None = None
) -> SyncSink:
    """A blocking SQLite sink."""
    from servomexlib.sinks import SqliteSink  # noqa: PLC0415

    return SyncSink(SqliteSink(Path(path), table=table), portal=portal)
