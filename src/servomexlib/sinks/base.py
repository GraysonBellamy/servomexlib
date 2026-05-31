"""Sink Protocol, ``sample_to_row`` flattener, and the :func:`pipe` driver.

A :class:`SampleSink` is the minimal shape the recorder's downstream consumer
needs: :meth:`open`, :meth:`write_many`, :meth:`close`, plus the async
context-manager methods. The in-tree sinks (memory/csv/jsonl/sqlite, plus optional
parquet/postgres) all satisfy it; third-party sinks slot in without touching
library code.

:func:`pipe` is the acquisition glue: it reads per-tick :class:`Sample` batches out
of the recorder's stream, buffers up to ``batch_size`` (or ``flush_interval``
seconds, whichever first), and flushes via ``sink.write_many``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

import anyio

from servomexlib._logging import get_logger
from servomexlib.streaming.recorder import AcquisitionSummary

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence
    from types import TracebackType
    from typing import Self

    from servomexlib.streaming.sample import Sample

__all__ = ["SampleSink", "pipe", "sample_to_row"]

_logger = get_logger("sinks")


class SampleSink(Protocol):
    """Minimal shape of an acquisition sink.

    Concrete sinks own their storage handle and typically follow:

    1. ``await sink.open()`` — allocate file descriptors / DB connections.
    2. ``await sink.write_many(samples)`` — one or more times.
    3. ``await sink.close()`` — flush and release (idempotent).

    The async context-manager methods give the ``async with sink:`` shape.
    """

    async def open(self) -> None:
        """Allocate the sink's backing resource (file handle, DB conn, ...)."""
        ...

    async def write_many(self, samples: Sequence[Sample]) -> None:
        """Append ``samples`` to the sink (``Sequence`` so the sink sees ``len``)."""
        ...

    async def close(self) -> None:
        """Flush and release the backing resource. Idempotent."""
        ...

    async def __aenter__(self) -> Self:
        """Open the sink and return ``self`` for chaining."""
        ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close the sink on exit."""
        ...


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _coerce_bool(value: bool) -> str:
    """Bools become ``"true"``/``"false"`` so SQLite doesn't pin the column INTEGER."""
    return "true" if value else "false"


def sample_to_row(sample: Sample) -> dict[str, float | int | str | None]:
    """Flatten a :class:`Sample` into a single row dict for tabular sinks.

    Long-format schema (one row per channel read), stable across all in-tree
    sinks. Error rows (a dropped/corrupt frame — :attr:`Sample.error` set,
    :attr:`Sample.reading` ``None``) carry ``None`` channel fields and a string
    ``error`` so a resync is still recorded rather than lost.

    The sample's ``raw`` payload is intentionally **not** in the row: bytes do not
    fit CSV / JSONL / SQLite affinities. Callers needing ``raw`` consume the
    :class:`Sample` directly via :class:`~servomexlib.sinks.memory.InMemorySink`.
    """
    reading = sample.reading
    channel = sample.channel.value if sample.channel is not None else None
    status = reading.status if reading is not None else None
    return {
        "device": sample.device,
        "channel": channel,
        "kind": reading.kind.value if reading is not None else None,
        "name": reading.name if reading is not None else None,
        "value": reading.value if reading is not None else None,
        "unit": reading.unit.value if reading is not None else None,
        "ok": _coerce_bool(status.ok) if status is not None else None,
        "fault": _coerce_bool(status.fault) if status is not None else None,
        "maintenance": _coerce_bool(status.maintenance) if status is not None else None,
        "calibrating": _coerce_bool(status.calibrating) if status is not None else None,
        "warming_up": _coerce_bool(status.warming_up) if status is not None else None,
        "protocol": sample.protocol.value,
        "monotonic_ns": sample.monotonic_ns,
        "received_at": sample.received_at.isoformat(),
        "requested_at": (
            sample.requested_at.isoformat() if sample.requested_at is not None else None
        ),
        "latency_s": sample.latency_s,
        "error": str(sample.error) if sample.error is not None else None,
    }


# ---------------------------------------------------------------------------
# pipe() driver
# ---------------------------------------------------------------------------


async def pipe(
    stream: AsyncIterator[Sequence[Sample]],
    sink: SampleSink,
    *,
    batch_size: int = 64,
    flush_interval: float = 1.0,
) -> AcquisitionSummary:
    r"""Drain ``stream`` into ``sink`` with buffered flushes.

    Reads per-tick batches from the recorder and accumulates individual
    :class:`Sample`\ s. A flush happens when the buffer reaches ``batch_size`` or
    ``flush_interval`` seconds have elapsed since the last flush, whichever first.
    On stream exhaustion any leftover buffer is flushed before returning.

    Args:
        stream: The async iterator yielded by :func:`~servomexlib.streaming.record`.
        sink: Any :class:`SampleSink`. Must already be open.
        batch_size: Buffer threshold in samples (not batches).
        flush_interval: Time threshold in seconds between flushes.

    Returns:
        An :class:`AcquisitionSummary` with ``samples_emitted`` set to the count
        handed to the sink (the sink-side view; ``samples_late`` / drift stay 0).

    Raises:
        ValueError: ``batch_size < 1`` or ``flush_interval <= 0``.
    """
    if batch_size < 1:
        raise ValueError(f"batch_size must be >= 1, got {batch_size!r}")
    if flush_interval <= 0:
        raise ValueError(f"flush_interval must be > 0, got {flush_interval!r}")

    started_at = datetime.now(UTC)
    emitted = 0
    buffer: list[Sample] = []
    last_flush = anyio.current_time()

    async def _flush() -> None:
        nonlocal emitted
        if not buffer:
            return
        await sink.write_many(buffer)
        emitted += len(buffer)
        buffer.clear()

    async for batch in stream:
        buffer.extend(batch)
        now = anyio.current_time()
        if len(buffer) >= batch_size or (now - last_flush) >= flush_interval:
            await _flush()
            last_flush = now

    await _flush()
    finished_at = datetime.now(UTC)
    _logger.info(
        "sinks.pipe_done sink=%s samples_emitted=%s duration_s=%.3f",
        type(sink).__name__,
        emitted,
        (finished_at - started_at).total_seconds(),
    )
    return AcquisitionSummary(
        started_at=started_at, finished_at=finished_at, samples_emitted=emitted
    )
