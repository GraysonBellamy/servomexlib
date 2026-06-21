"""Absolute-target recorder — ``record()`` emits timed :class:`Sample` batches.

:func:`record` is the drift-free acquisition primitive for Modbus ``POLL`` mode
. It drives a :class:`~servomexlib.streaming.poll_source.PollSource`
(an opened :class:`~servomexlib.devices.analyzer.Analyzer` or a
:class:`~servomexlib.manager.ServomexManager`) at an absolute cadence and
publishes each tick's :class:`Sample` rows into an ``anyio`` memory-object stream
as per-tick batches. Continuous mode does not use the recorder — its frames are
unsolicited, so it fans out through the passive ``AUTOPRINT`` subscribe instead.

Key invariants (shared family shape):

- **Absolute-target scheduling.** Targets are ``start + n * period`` computed from
  :func:`anyio.current_time` at entry, so drift is bounded to one tick and never
  accumulates; overruns skip missed slots and bump ``samples_late``.
- **Structured concurrency.** The producer task lives strictly inside the async CM
  body; the CM cancels and joins it before returning.
- **Wall-clock provenance.** ``Sample.received_at`` carries acquisition wall-clock
  (set by the source), used for sink timestamps, never for scheduling.
- **Backpressure.** ``buffer_size`` sizes the stream; :class:`OverflowPolicy`
  decides what happens when the consumer falls behind.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING, cast

import anyio

from servomexlib._logging import get_logger
from servomexlib.errors import ServomexConnectionError
from servomexlib.streaming.poll_source import PollSource
from servomexlib.streaming.sample import Sample

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable

    from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream

__all__ = [
    "AcquisitionSummary",
    "OverflowPolicy",
    "PollSource",
    "Recording",
    "record",
]

#: Reconnect backoff schedule for ``auto_reconnect`` — small first, capped at 30s.
_RECONNECT_BACKOFF_S: tuple[float, ...] = (0.5, 1.0, 2.0, 5.0, 10.0, 30.0)
_MS = 1_000.0

_logger = get_logger("streaming")


class OverflowPolicy(Enum):
    """What ``record()`` does when the receive-stream buffer is full.

    The producer runs on an absolute schedule; the consumer drains at its own
    pace. Slow consumers create backpressure — this knob picks the response.
    """

    BLOCK = "block"
    """Await the slow consumer (default). Silent drops are surprising in
    acquisition, so the recorder blocks rather than discarding."""

    DROP_NEWEST = "drop_newest"
    """Drop the batch about to be enqueued. Counted as late."""

    DROP_OLDEST = "drop_oldest"
    """Evict the oldest queued batch and enqueue the newest. For real-time
    monitoring where the latest reading matters most. Each eviction is late."""


@dataclass(slots=True)
class AcquisitionSummary:
    """Per-run summary, owned and mutated by the recorder (sole writer).

    Counters update in place during the run so progress-polling consumers see
    live values; consumers treat it as read-only. ``finished_at`` and the
    percentile fields are populated on context-manager exit.

    Attributes:
        started_at: Wall-clock at the first scheduled tick.
        finished_at: Wall-clock at producer shutdown, or ``None`` while running.
        samples_emitted: Per-tick batches pushed onto the stream (a tick whose
            reads all failed still counts as one emitted batch).
        samples_late: Ticks that missed their slot (overrun, overflow drop, or a
            reconnect gap).
        max_drift_ms: Largest positive drift of an emitted batch from its target.
        tick_duration_ms_p50: Median ``poll_samples`` duration (set on exit).
        tick_duration_ms_p99: 99th-percentile ``poll_samples`` duration (on exit).
        disconnects: ``ServomexConnectionError`` events absorbed under
            ``auto_reconnect``; ``0`` when it was off.
    """

    started_at: datetime
    finished_at: datetime | None = None
    samples_emitted: int = 0
    samples_late: int = 0
    max_drift_ms: float = 0.0
    tick_duration_ms_p50: float = 0.0
    tick_duration_ms_p99: float = 0.0
    disconnects: int = 0


@dataclass(slots=True)
class Recording[T]:
    """Container yielded by :func:`record` — stream + live summary + rate.

    Shares the cross-family shape (``stream`` / ``summary`` / ``rate_hz``) so
    downstream consumers are vendor-agnostic. For servomexlib the payload is
    ``Recording[Sequence[Sample]]`` — per-tick batches.

    Attributes:
        stream: Async iterator of per-tick :class:`Sample` batches.
        summary: Live :class:`AcquisitionSummary` (recorder mutates, consumer
            reads); ``summary.finished_at`` is set on CM exit.
        rate_hz: The cadence captured at ``record()`` entry.
    """

    stream: AsyncIterator[T]
    summary: AcquisitionSummary
    rate_hz: float


@asynccontextmanager
async def record(
    source: PollSource,
    *,
    rate_hz: float,
    duration: float | None = None,
    names: Sequence[str] | None = None,
    timeout: float | None = None,
    overflow: OverflowPolicy = OverflowPolicy.BLOCK,
    buffer_size: int = 64,
    auto_reconnect: bool = False,
    reconnect_factory: Callable[[], Awaitable[PollSource]] | None = None,
) -> AsyncGenerator[Recording[Sequence[Sample]]]:
    """Record polled samples into a receive stream at an absolute cadence.

    Usage::

        async with record(analyzer, rate_hz=2, duration=10) as rec:
            async for batch in rec.stream:
                for sample in batch:
                    print(sample.channel, sample.value)

    Args:
        source: Any :class:`PollSource` (an :class:`Analyzer` or a
            :class:`ServomexManager`).
        rate_hz: Target cadence; ``target[n] = start + n / rate_hz``. Must be > 0.
        duration: Total acquisition seconds, or ``None`` for "until the caller
            exits the CM".
        names: Subset of device names to poll (manager only); ``None`` polls all.
        timeout: Per-poll I/O ceiling passed to ``source.poll_samples``.
        overflow: Backpressure policy when the buffer is full.
        buffer_size: Receive-stream capacity, in per-tick batches.
        auto_reconnect: Treat :class:`ServomexConnectionError` from the source as
            a transient drop: log, back off, optionally rebuild via
            ``reconnect_factory``, and keep going (missed ticks count late).
        reconnect_factory: Rebuilds the source after a disconnect when supplied.

    Yields:
        A :class:`Recording[Sequence[Sample]]`.

    Raises:
        ValueError: ``rate_hz <= 0``, ``duration <= 0``, or ``buffer_size < 1``.
    """
    if rate_hz <= 0:
        raise ValueError(f"rate_hz must be > 0, got {rate_hz!r}")
    if duration is not None and duration <= 0:
        raise ValueError(f"duration must be > 0 or None, got {duration!r}")
    if buffer_size < 1:
        raise ValueError(f"buffer_size must be >= 1, got {buffer_size!r}")

    period = 1.0 / rate_hz
    total_ticks = None if duration is None else max(1, round(duration * rate_hz))

    send_stream, receive_stream = anyio.create_memory_object_stream[Sequence[Sample]](
        max_buffer_size=buffer_size,
    )
    # Producer-side clone for DROP_OLDEST eviction, off the consumer's iterator.
    drop_rx = receive_stream.clone()

    started_at = datetime.now(UTC)
    summary = AcquisitionSummary(started_at=started_at)
    tick_durations_ms: list[float] = []
    _logger.info(
        "recorder.start rate_hz=%s duration_s=%s overflow=%s buffer_size=%s names=%s",
        rate_hz,
        duration,
        overflow.value,
        buffer_size,
        list(names) if names is not None else None,
    )

    try:
        async with anyio.create_task_group() as tg, receive_stream:

            async def _producer_entrypoint() -> None:
                await _run_producer(
                    source,
                    send_stream,
                    drop_rx,
                    names,
                    timeout,
                    period,
                    total_ticks,
                    overflow,
                    summary,
                    tick_durations_ms,
                    auto_reconnect=auto_reconnect,
                    reconnect_factory=reconnect_factory,
                )

            _ = tg.start_soon(_producer_entrypoint)
            try:
                yield Recording(stream=receive_stream, summary=summary, rate_hz=rate_hz)
            finally:
                tg.cancel()
    except BaseExceptionGroup as eg:
        # A lone producer failure (e.g. ServomexConnectionError without
        # auto_reconnect) surfaces as a single-member group from the task group;
        # collapse it so callers catch the concrete error, not the wrapper.
        raise _collapse(eg) from None
    finally:
        summary.finished_at = datetime.now(UTC)
        summary.tick_duration_ms_p50, summary.tick_duration_ms_p99 = _tick_percentiles(
            tick_durations_ms
        )
        _logger.info(
            "recorder.stop emitted=%s late=%s max_drift_ms=%.3f tick_p50_ms=%.3f tick_p99_ms=%.3f",
            summary.samples_emitted,
            summary.samples_late,
            summary.max_drift_ms,
            summary.tick_duration_ms_p50,
            summary.tick_duration_ms_p99,
        )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _collapse(eg: BaseExceptionGroup[BaseException]) -> BaseException:
    """Collapse a single-member (possibly nested) exception group to its leaf.

    A group with more than one exception is returned unchanged.
    """
    if len(eg.exceptions) == 1:
        inner = eg.exceptions[0]
        if isinstance(inner, BaseExceptionGroup):
            return _collapse(cast("BaseExceptionGroup[BaseException]", inner))
        return inner
    return eg


def _tick_percentiles(values: list[float]) -> tuple[float, float]:
    """Compute (p50, p99) over ``values`` with linear interpolation.

    Returns ``(0.0, 0.0)`` for empty input; both equal the lone value for a
    single sample. Otherwise the ``i = p * (n - 1)`` convention (numpy's default
    ``linear`` method).
    """
    if not values:
        return 0.0, 0.0
    sorted_v = sorted(values)
    n = len(sorted_v)
    if n == 1:
        return sorted_v[0], sorted_v[0]

    def _q(p: float) -> float:
        k = (n - 1) * p
        f = int(k)
        c = min(f + 1, n - 1)
        if f == c:
            return sorted_v[f]
        return sorted_v[f] * (c - k) + sorted_v[c] * (k - f)

    return _q(0.5), _q(0.99)


async def _run_producer(
    source: PollSource,
    send_stream: MemoryObjectSendStream[Sequence[Sample]],
    drop_rx: MemoryObjectReceiveStream[Sequence[Sample]],
    names: Sequence[str] | None,
    timeout: float | None,
    period: float,
    total_ticks: int | None,
    overflow: OverflowPolicy,
    summary: AcquisitionSummary,
    tick_durations_ms: list[float],
    *,
    auto_reconnect: bool = False,
    reconnect_factory: Callable[[], Awaitable[PollSource]] | None = None,
) -> None:
    """Drive the absolute-cadence poll loop.

    Scheduling uses :func:`anyio.current_time` so :func:`anyio.sleep_until`
    interprets targets against the same clock. A
    :class:`~servomexlib.errors.ServomexConnectionError` under ``auto_reconnect``
    is treated as a transient gap (counted late, backed off, optionally rebuilt).
    """
    start = anyio.current_time()
    tick = 0
    backoff_idx = 0
    active_source = source
    try:
        while total_ticks is None or tick < total_ticks:
            target = start + tick * period
            now = anyio.current_time()
            if now > target + period:
                # Overran by more than a period — skip to the next valid slot.
                missed = int((now - target) / period)
                summary.samples_late += missed
                tick += missed
                target = start + tick * period
            if anyio.current_time() < target:
                await anyio.sleep_until(target)

            tick_start = time.monotonic()
            try:
                batch = await active_source.poll_samples(names=names, timeout=timeout)
            except ServomexConnectionError as exc:
                if not auto_reconnect:
                    raise
                summary.samples_late += 1
                summary.disconnects += 1
                wait_s = _RECONNECT_BACKOFF_S[min(backoff_idx, len(_RECONNECT_BACKOFF_S) - 1)]
                _logger.warning(
                    "recorder.disconnected reason=%s tick=%d backoff_s=%.2f", exc, tick, wait_s
                )
                await anyio.sleep(wait_s)
                if reconnect_factory is not None:
                    try:
                        active_source = await reconnect_factory()
                        _logger.info("recorder.reconnected tick=%d", tick)
                        backoff_idx = 0
                    except ServomexConnectionError:
                        backoff_idx += 1
                else:
                    backoff_idx += 1
                tick += 1
                continue

            backoff_idx = 0
            tick_durations_ms.append((time.monotonic() - tick_start) * _MS)
            drift_s = anyio.current_time() - target
            summary.max_drift_ms = max(summary.max_drift_ms, drift_s * _MS)

            await _publish(send_stream, drop_rx, batch, overflow, summary)
            tick += 1
    finally:
        await send_stream.aclose()
        await drop_rx.aclose()


async def _publish(
    send_stream: MemoryObjectSendStream[Sequence[Sample]],
    drop_rx: MemoryObjectReceiveStream[Sequence[Sample]],
    batch: Sequence[Sample],
    overflow: OverflowPolicy,
    summary: AcquisitionSummary,
) -> None:
    """Enqueue ``batch`` per the configured :class:`OverflowPolicy`."""
    if overflow is OverflowPolicy.BLOCK:
        await send_stream.send(batch)
        summary.samples_emitted += 1
        return
    if overflow is OverflowPolicy.DROP_NEWEST:
        try:
            send_stream.send_nowait(batch)
        except anyio.WouldBlock:
            summary.samples_late += 1
            _logger.warning("recorder.drop_newest reason=consumer_backpressure")
            return
        summary.samples_emitted += 1
        return
    if overflow is OverflowPolicy.DROP_OLDEST:
        try:
            send_stream.send_nowait(batch)
            summary.samples_emitted += 1
            return
        except anyio.WouldBlock:
            pass
        while True:
            try:
                drop_rx.receive_nowait()
                summary.samples_late += 1
                _logger.warning("recorder.drop_oldest reason=consumer_backpressure")
            except anyio.WouldBlock:
                pass  # consumer won the race and freed space
            try:
                send_stream.send_nowait(batch)
                summary.samples_emitted += 1
                return
            except anyio.WouldBlock:
                continue  # still full; evict another
    raise AssertionError(f"unreachable overflow policy: {overflow!r}")  # pragma: no cover
