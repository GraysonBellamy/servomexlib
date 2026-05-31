"""Passive continuous-mode client.

Continuous mode has no request channel — the analyser just emits frames. The
:class:`ContinuousClient` runs a background receive loop that parses each frame,
caches the latest, and fans out :class:`Sample` rows to live ``stream()``
subscribers. ``read_frame`` / ``read_channel`` return the cached latest
immediately (or wait for the *next* frame when ``wait_fresh=True``); a corrupt
frame is dropped from the cache and surfaced to subscribers as an error
:class:`Sample`, never crashing the loop.
"""

from __future__ import annotations

import math
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import anyio

from servomexlib.devices.capability import Capability
from servomexlib.devices.models import ChannelInfo, DeviceInfo, Frame, Sample
from servomexlib.errors import (
    ErrorContext,
    ServomexConnectionError,
    ServomexError,
    ServomexTimeoutError,
    ServomexValidationError,
)
from servomexlib.protocol.base import ProtocolKind
from servomexlib.protocol.continuous.parser import parse_frame
from servomexlib.streaming.stream_session import StreamingSession, StreamMode
from servomexlib.transport.base import SerialSettings

if TYPE_CHECKING:
    from anyio.streams.memory import MemoryObjectSendStream

    from servomexlib.devices.models import AnalyserStatus, ChannelStatus, Reading
    from servomexlib.registry.channels import ChannelId
    from servomexlib.transport.base import Transport

_CRLF = b"\r\n"
#: Per-read ceiling on the background loop. A frame should arrive within a few
#: cadence intervals; on timeout the loop simply waits again (cancelled at close).
_LOOP_READ_TIMEOUT = 30.0


class ContinuousClient:
    """Background-receive client for unsolicited continuous-ASCII frames."""

    kind = ProtocolKind.CONTINUOUS_ASCII
    capabilities = Capability.READ_CHANNELS | Capability.READ_STATUS | Capability.IDENTIFY

    def __init__(self, transport: Transport, *, device: str = "", timeout: float = 1.0) -> None:
        self._transport = transport
        self._device = device
        self._timeout = timeout
        self._latest: Frame | None = None
        self._fresh = anyio.Event()
        self._subscribers: set[MemoryObjectSendStream[Sample]] = set()
        self._bad_frames = 0
        self._first_error: ServomexError | None = None

    @property
    def latest(self) -> Frame | None:
        """The most recent good frame, or ``None`` before the first arrives."""
        return self._latest

    @property
    def bad_frame_count(self) -> int:
        """How many frames have been dropped for parse/checksum failures."""
        return self._bad_frames

    # ------------------------------------------------------------------ loop

    async def run(self) -> None:
        """Background receive loop. Runs until cancelled or the transport closes."""
        while True:
            try:
                raw = await self._transport.read_until(_CRLF, timeout=_LOOP_READ_TIMEOUT)
            except ServomexTimeoutError:
                continue  # no frame this window; keep listening
            except ServomexConnectionError:
                return  # transport closed / lost — stop the loop cleanly
            await self._handle(raw)

    async def _handle(self, raw: bytes) -> None:
        try:
            frame = parse_frame(raw)
        except ServomexError as exc:
            self._bad_frames += 1
            if self._first_error is None:
                self._first_error = exc
            await self._broadcast_error(exc)
            return
        self._latest = frame
        self._signal_fresh()
        await self._broadcast_frame(frame)

    def _signal_fresh(self) -> None:
        previous = self._fresh
        self._fresh = anyio.Event()
        previous.set()

    # ------------------------------------------------------------------ reads

    async def read_frame(self, *, wait_fresh: bool = False, timeout: float | None = None) -> Frame:
        """Return the latest cached frame (or wait for the next when ``wait_fresh``)."""
        if wait_fresh or self._latest is None:
            event = self._fresh
            try:
                with anyio.fail_after(timeout if timeout is not None else self._timeout):
                    await event.wait()
            except TimeoutError as exc:
                raise ServomexTimeoutError(
                    f"no continuous frame within {timeout or self._timeout}s",
                    context=ErrorContext(
                        port=self._transport.label,
                        protocol=ProtocolKind.CONTINUOUS_ASCII,
                    ),
                ) from exc
        frame = self._latest
        if frame is None:  # pragma: no cover - the wait above guarantees a frame
            raise ServomexTimeoutError(
                "continuous frame signalled but cache was empty",
                context=ErrorContext(port=self._transport.label),
            )
        return frame

    async def read_channel(self, channel: ChannelId, *, timeout: float | None = None) -> Reading:
        """Return one channel's reading from the latest frame."""
        frame = await self.read_frame(timeout=timeout)
        return frame.channel(channel)

    async def analyser_status(self, *, timeout: float | None = None) -> AnalyserStatus:
        """Return the analyser-level status from the latest frame."""
        return (await self.read_frame(timeout=timeout)).analyser

    async def status(self, channel: ChannelId, *, timeout: float | None = None) -> ChannelStatus:
        """Return one channel's status from the latest frame."""
        return (await self.read_channel(channel, timeout=timeout)).status

    async def identify(self, *, timeout: float | None = None) -> DeviceInfo:
        """Derive device identity from the first frame's populated slots."""
        frame = await self.read_frame(timeout=timeout)
        channels = tuple(
            ChannelInfo(
                channel=reading.channel,
                kind=reading.kind,
                name=reading.name,
                unit=reading.unit,
            )
            for reading in frame.readings
        )
        return DeviceInfo(
            model="4000-series",
            channels=channels,
            protocol=ProtocolKind.CONTINUOUS_ASCII,
            address=0,
            serial_settings=SerialSettings(port=self._transport.label),
        )

    # ------------------------------------------------------------------ streaming

    def stream(self, *, mode: StreamMode | None = None) -> StreamingSession:
        """Subscribe to the broadcast as a :class:`StreamingSession`.

        Continuous mode is passive, so only ``AUTOPRINT`` is valid; ``POLL``
        raises :class:`ServomexValidationError`.
        """
        resolved = mode if mode is not None else StreamMode.AUTOPRINT
        if resolved is not StreamMode.AUTOPRINT:
            raise ServomexValidationError(
                f"continuous mode only supports AUTOPRINT streaming, not {resolved.value}",
                context=ErrorContext(protocol=ProtocolKind.CONTINUOUS_ASCII),
            )
        send: MemoryObjectSendStream[Sample]
        send, recv = anyio.create_memory_object_stream[Sample](math.inf)
        self._subscribers.add(send)
        return StreamingSession(recv, mode=resolved, on_close=lambda: self._unsubscribe(send))

    def _unsubscribe(self, send: MemoryObjectSendStream[Sample]) -> None:
        self._subscribers.discard(send)
        send.close()

    async def _broadcast_frame(self, frame: Frame) -> None:
        for sample in frame.as_samples(device=self._device):
            self._emit(sample)

    async def _broadcast_error(self, error: ServomexError) -> None:
        if not self._subscribers:
            return
        sample = Sample(
            device=self._device,
            channel=None,
            reading=None,
            protocol=ProtocolKind.CONTINUOUS_ASCII,
            monotonic_ns=time.monotonic_ns(),
            received_at=datetime.now(UTC),
            error=error,
        )
        self._emit(sample)

    def _emit(self, sample: Sample) -> None:
        for send in list(self._subscribers):
            try:
                send.send_nowait(sample)
            except anyio.WouldBlock:  # pragma: no cover - unbounded buffer
                pass
            except anyio.ClosedResourceError:
                self._subscribers.discard(send)

    # ------------------------------------------------------------------ lifecycle

    async def aclose(self) -> None:
        """Close all subscribers and the transport."""
        for send in list(self._subscribers):
            send.close()
        self._subscribers.clear()
        await self._transport.aclose()


__all__ = ["ContinuousClient"]
