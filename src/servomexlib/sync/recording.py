"""Synchronous recording helper.

:func:`record_to_sink` runs the full async :func:`~servomexlib.streaming.record` →
:func:`~servomexlib.sinks.pipe` pipeline to completion on a
:class:`~servomexlib.sync.portal.SyncPortal` and returns the
:class:`~servomexlib.streaming.AcquisitionSummary`. Running the whole pipeline in a
single portal call keeps the recorder's task group within one loop task, avoiding
cross-task cancel-scope issues. A finite ``duration`` is required so it terminates.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from servomexlib.sinks.base import pipe
from servomexlib.streaming.recorder import record
from servomexlib.sync.portal import SyncPortal

if TYPE_CHECKING:
    from collections.abc import Sequence

    from servomexlib.sinks.base import SampleSink
    from servomexlib.streaming.poll_source import PollSource
    from servomexlib.streaming.recorder import AcquisitionSummary

__all__ = ["record_to_sink"]


def record_to_sink(
    source: PollSource,
    sink: SampleSink,
    *,
    rate_hz: float,
    duration: float,
    names: Sequence[str] | None = None,
    timeout: float | None = None,
    batch_size: int = 64,
    flush_interval: float = 1.0,
    backend: str = "asyncio",
    portal: SyncPortal | None = None,
) -> AcquisitionSummary:
    """Record ``source`` into ``sink`` for ``duration`` seconds, blocking until done.

    Args:
        source: An async :class:`PollSource` (an ``Analyzer`` or ``ServomexManager``).
        sink: An async :class:`SampleSink`; opened and closed by this call.
        rate_hz: Poll cadence.
        duration: Acquisition seconds (required — the call blocks until it elapses).
        names: Subset of device names (manager only).
        timeout: Per-poll I/O ceiling.
        batch_size: Sink flush threshold in samples.
        flush_interval: Sink flush interval in seconds.
        backend: AnyIO backend for the private portal (ignored if ``portal`` given).
        portal: Reuse an existing portal instead of starting one.

    Returns:
        The sink-side :class:`AcquisitionSummary`.
    """
    owns_portal = portal is None
    active = portal if portal is not None else SyncPortal(backend=backend)
    if owns_portal:
        active.__enter__()
    try:
        return active.call(
            _run,
            source,
            sink,
            rate_hz,
            duration,
            names,
            timeout,
            batch_size,
            flush_interval,
        )
    finally:
        if owns_portal:
            active.__exit__(None, None, None)


async def _run(
    source: PollSource,
    sink: SampleSink,
    rate_hz: float,
    duration: float,
    names: Sequence[str] | None,
    timeout: float | None,
    batch_size: int,
    flush_interval: float,
) -> AcquisitionSummary:
    async with (
        sink,
        record(
            source, rate_hz=rate_hz, duration=duration, names=names, timeout=timeout
        ) as recording,
    ):
        return await pipe(
            recording.stream, sink, batch_size=batch_size, flush_interval=flush_interval
        )
