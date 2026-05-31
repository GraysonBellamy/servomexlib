"""The :class:`Analyzer` facade — the protocol-neutral public API.

One uniform method set across all three modes, routed through the
:class:`~servomexlib.devices.session.Session` gate ladder. Reads work everywhere;
the autocalibration control surface is gated (``confirm=True`` + ``AUTOCAL``
capability) and so fails cleanly in continuous mode *before* any I/O.

Built by :func:`~servomexlib.devices.factory.open_device`; used as an async
context manager that starts (and, for continuous, runs the background receive
loop of) the session.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, cast

import anyio

from servomexlib.devices.session import Session
from servomexlib.errors import ServomexValidationError
from servomexlib.registry.channels import ChannelId
from servomexlib.streaming.recorder import record
from servomexlib.streaming.sample import Sample
from servomexlib.streaming.stream_session import StreamingSession, StreamMode

if TYPE_CHECKING:
    from collections.abc import Sequence
    from types import TracebackType
    from typing import Self

    from anyio.streams.memory import MemoryObjectSendStream

    from servomexlib.devices.capability import Capability
    from servomexlib.devices.models import (
        AnalyserStatus,
        CalibrationProgress,
        ChannelStatus,
        DeviceInfo,
        Frame,
        Reading,
    )
    from servomexlib.protocol.base import ProtocolClient, ProtocolKind


class Analyzer:
    """Protocol-neutral async-context-manager facade over one analyser."""

    def __init__(
        self,
        client: ProtocolClient,
        *,
        device: str = "",
        identify_on_enter: bool = False,
    ) -> None:
        self._client = client
        self._device = device
        self._session = Session(client)
        self._identify_on_enter = identify_on_enter
        self._info: DeviceInfo | None = None

    @property
    def protocol(self) -> ProtocolKind:
        """The active wire protocol."""
        return self._client.kind

    @property
    def capabilities(self) -> Capability:
        """The active client's capability set."""
        return self._client.capabilities

    @property
    def info(self) -> DeviceInfo | None:
        """The cached :class:`DeviceInfo`, if ``identify`` has run."""
        return self._info

    @property
    def dropped_frames(self) -> int:
        """Count of frames dropped for parse/checksum failures (continuous resync)."""
        return self._session.dropped_frames

    async def __aenter__(self) -> Self:
        await self._session.__aenter__()
        if self._identify_on_enter:
            self._info = await self._session.identify()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self._session.__aexit__(exc_type, exc, tb)

    # ------------------------------------------------------------------ reads

    async def poll(self, *, wait_fresh: bool = False, timeout: float | None = None) -> Frame:
        """Return one frame — all channels + analyser status, one tick.

        ``wait_fresh=True`` waits for the *next* continuous broadcast instead of
        returning the cached latest; it is a no-op for the polled Modbus path.
        """
        return await self._session.read_frame(wait_fresh=wait_fresh, timeout=timeout)

    async def read_channel(
        self, channel: ChannelId | str, *, timeout: float | None = None
    ) -> Reading:
        """Return one channel's latest reading."""
        return await self._session.read_channel(_coerce_channel(channel), timeout=timeout)

    async def read_all(self, *, timeout: float | None = None) -> dict[ChannelId, Reading]:
        """Return every channel's latest reading keyed by id."""
        frame = await self._session.read_frame(timeout=timeout)
        return {reading.channel: reading for reading in frame.readings}

    async def status(
        self, channel: ChannelId | str, *, timeout: float | None = None
    ) -> ChannelStatus:
        """Return one channel's latest status."""
        return await self._session.status(_coerce_channel(channel), timeout=timeout)

    async def analyser_status(self, *, timeout: float | None = None) -> AnalyserStatus:
        """Return the analyser-level status."""
        return await self._session.analyser_status(timeout=timeout)

    async def identify(self, *, timeout: float | None = None) -> DeviceInfo:
        """Return device identity, caching it on first call."""
        self._info = await self._session.identify(timeout=timeout)
        return self._info

    def snapshot(self) -> Frame:
        """Return the cached latest frame without any I/O."""
        return self._session.snapshot()

    async def poll_samples(
        self, *, names: Sequence[str] | None = None, timeout: float | None = None
    ) -> list[Sample]:
        """Poll one frame and fan it out into long-format samples (``PollSource``).

        Makes the analyser a :class:`~servomexlib.streaming.poll_source.PollSource`
        the recorder can drive. ``names`` is ignored for a solo analyser (it is the
        manager's device-subset selector).
        """
        del names
        frame = await self._session.read_frame(timeout=timeout)
        return frame.as_samples(device=self._device)

    # ------------------------------------------------------------------ control (gated)

    async def start_calibration(
        self, group: int, *, confirm: bool = False, timeout: float | None = None
    ) -> None:
        """Start autocalibration for ``group`` (Modbus only; requires ``confirm=True``)."""
        await self._session.start_calibration(group, confirm=confirm, timeout=timeout)

    async def stop_calibration(
        self, *, confirm: bool = False, timeout: float | None = None
    ) -> None:
        """Stop all autocalibration (Modbus only; requires ``confirm=True``)."""
        await self._session.stop_calibration(confirm=confirm, timeout=timeout)

    async def calibration_status(
        self, group: int = 1, *, timeout: float | None = None
    ) -> CalibrationProgress:
        """Return autocalibration progress for ``group`` (Modbus only)."""
        return await self._session.calibration_status(group, timeout=timeout)

    # ------------------------------------------------------------------ streaming

    def stream(
        self, *, mode: StreamMode | None = None, rate_hz: float | None = None
    ) -> StreamingSession:
        """Stream samples — ``AUTOPRINT`` (continuous) or ``POLL`` (Modbus).

        Defaults the mode per protocol. Continuous mode subscribes passively to the
        unsolicited broadcast (``rate_hz`` is ignored — the analyser sets the
        cadence). Modbus mode drives the drift-free
        :func:`~servomexlib.streaming.recorder.record` loop at ``rate_hz``
        (default ``1.0``) inside a task group the returned session owns.

        Use it as an async context manager so the poll loop starts and stops with
        the session::

            async with anz.stream(rate_hz=2) as samples:
                async for sample in samples:
                    ...
        """
        client_stream = getattr(self._client, "stream", None)
        if callable(client_stream):  # continuous: passive broadcast subscribe
            return cast("StreamingSession", client_stream(mode=mode))
        resolved = mode if mode is not None else StreamMode.POLL
        if resolved is not StreamMode.POLL:
            raise ServomexValidationError(
                f"{self.protocol.value} mode only supports POLL streaming, not {resolved.value}",
            )
        return self._poll_stream(rate_hz if rate_hz is not None else 1.0)

    def _poll_stream(self, rate_hz: float) -> StreamingSession:
        send: MemoryObjectSendStream[Sample]
        send, recv = anyio.create_memory_object_stream[Sample](math.inf)

        async def _producer() -> None:
            async with send, record(self, rate_hz=rate_hz) as recording:
                async for batch in recording.stream:
                    for sample in batch:
                        await send.send(sample)

        return StreamingSession(recv, mode=StreamMode.POLL, producer=_producer)


def _coerce_channel(channel: ChannelId | str) -> ChannelId:
    if isinstance(channel, ChannelId):
        return channel
    try:
        return ChannelId(channel)
    except ValueError as exc:
        raise ServomexValidationError(f"unknown channel id {channel!r}") from exc


__all__ = ["Analyzer"]
