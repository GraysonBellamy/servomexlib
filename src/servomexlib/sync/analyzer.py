"""Synchronous facade — :class:`Servomex` / :class:`SyncAnalyzer`.

A thin, blocking mirror of the async :class:`~servomexlib.devices.analyzer.Analyzer`
for scripts, notebooks, and the REPL. Every async method has a one-line sync twin
that marshals through a :class:`~servomexlib.sync.portal.SyncPortal`::

    from servomexlib.sync import Servomex

    with Servomex.open("COM11", protocol="modbus_rtu", address=30) as anz:
        print(anz.poll())
        anz.start_calibration(1, confirm=True)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Self

from servomexlib.devices.factory import open_device
from servomexlib.protocol.base import ProtocolKind
from servomexlib.sync.portal import SyncPortal

if TYPE_CHECKING:
    from contextlib import AbstractContextManager
    from types import TracebackType

    from servomexlib.devices.analyzer import Analyzer
    from servomexlib.devices.capability import Capability
    from servomexlib.devices.models import (
        AnalyserStatus,
        CalibrationProgress,
        ChannelStatus,
        DeviceInfo,
        Frame,
        Reading,
    )
    from servomexlib.registry.channels import ChannelId
    from servomexlib.streaming.stream_session import StreamMode
    from servomexlib.transport.base import SerialSettings, Transport


class SyncStreamingSession:
    """Blocking iterator over an async streaming session, marshalled via the portal."""

    def __init__(self, portal: SyncPortal, session: object) -> None:
        self._portal = portal
        self._session = session

    def __enter__(self) -> Self:
        # Start the session (a no-op for the passive continuous subscribe; for the
        # Modbus POLL path this launches the recorder producer).
        self._portal.call(self._session.__aenter__)  # type: ignore[attr-defined]
        return self

    def __exit__(self, *exc: object) -> None:
        self._portal.call(self._session.aclose)  # type: ignore[attr-defined]

    def __iter__(self) -> Self:
        return self

    def __next__(self) -> object:
        try:
            return self._portal.call(self._session.__anext__)  # type: ignore[attr-defined]
        except StopAsyncIteration:
            raise StopIteration from None


class SyncAnalyzer:
    """Blocking mirror of :class:`~servomexlib.devices.analyzer.Analyzer`."""

    def __init__(
        self,
        portal: SyncPortal,
        analyzer: Analyzer,
        managed: AbstractContextManager[Analyzer],
        *,
        owns_portal: bool = True,
    ) -> None:
        self._portal = portal
        self._analyzer = analyzer
        self._managed = managed
        self._owns_portal = owns_portal

    # ------------------------------------------------------------------ lifecycle

    @classmethod
    def open(
        cls,
        port: str | Transport,
        *,
        protocol: ProtocolKind | str = ProtocolKind.AUTO,
        address: int = 1,
        serial_settings: SerialSettings | None = None,
        timeout: float = 1.0,
        identify: bool = True,
        backend: str = "asyncio",
        portal: SyncPortal | None = None,
    ) -> SyncAnalyzer:
        """Open an analyser synchronously and return an entered :class:`SyncAnalyzer`.

        Pass ``portal`` to share an existing loop (e.g. one already hosting an
        in-process fake); otherwise a private background loop is started and torn
        down with the analyzer.
        """
        resolved = ProtocolKind(protocol)  # accepts a ProtocolKind or its str value
        owns_portal = portal is None
        active = portal if portal is not None else SyncPortal(backend=backend)
        if owns_portal:
            active.__enter__()
        try:
            analyzer = active.call(
                open_device,
                port,
                protocol=resolved,
                address=address,
                serial_settings=serial_settings,
                timeout=timeout,
                identify=identify,
            )
            # Enter via the portal's wrapper so __aenter__/__aexit__ share one task.
            managed = active.wrap_async_context_manager(analyzer)
            managed.__enter__()
        except BaseException:
            if owns_portal:
                active.__exit__(None, None, None)
            raise
        return cls(active, analyzer, managed, owns_portal=owns_portal)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            self._managed.__exit__(exc_type, exc, tb)
        finally:
            if self._owns_portal:
                self._portal.__exit__(exc_type, exc, tb)

    # ------------------------------------------------------------------ properties

    @property
    def protocol(self) -> ProtocolKind:
        """The active wire protocol."""
        return self._analyzer.protocol

    @property
    def capabilities(self) -> Capability:
        """The active client's capability set."""
        return self._analyzer.capabilities

    @property
    def info(self) -> DeviceInfo | None:
        """The cached :class:`DeviceInfo`, if ``identify`` has run."""
        return self._analyzer.info

    @property
    def dropped_frames(self) -> int:
        """Count of frames dropped for parse/checksum failures."""
        return self._analyzer.dropped_frames

    # ------------------------------------------------------------------ reads

    def poll(self, *, wait_fresh: bool = False, timeout: float | None = None) -> Frame:
        """Return one frame (all channels + analyser status)."""
        return self._portal.call(self._analyzer.poll, wait_fresh=wait_fresh, timeout=timeout)

    def read_channel(self, channel: ChannelId | str, *, timeout: float | None = None) -> Reading:
        """Return one channel's latest reading."""
        return self._portal.call(self._analyzer.read_channel, channel, timeout=timeout)

    def read_all(self, *, timeout: float | None = None) -> dict[ChannelId, Reading]:
        """Return every channel's latest reading keyed by id."""
        return self._portal.call(self._analyzer.read_all, timeout=timeout)

    def status(self, channel: ChannelId | str, *, timeout: float | None = None) -> ChannelStatus:
        """Return one channel's latest status."""
        return self._portal.call(self._analyzer.status, channel, timeout=timeout)

    def analyser_status(self, *, timeout: float | None = None) -> AnalyserStatus:
        """Return the analyser-level status."""
        return self._portal.call(self._analyzer.analyser_status, timeout=timeout)

    def identify(self, *, timeout: float | None = None) -> DeviceInfo:
        """Return device identity, caching it."""
        return self._portal.call(self._analyzer.identify, timeout=timeout)

    def snapshot(self) -> Frame:
        """Return the cached latest frame without any I/O."""
        return self._analyzer.snapshot()

    # ------------------------------------------------------------------ control

    def start_calibration(
        self, group: int, *, confirm: bool = False, timeout: float | None = None
    ) -> None:
        """Start autocalibration for ``group`` (Modbus only; requires ``confirm=True``)."""
        self._portal.call(self._analyzer.start_calibration, group, confirm=confirm, timeout=timeout)

    def stop_calibration(self, *, confirm: bool = False, timeout: float | None = None) -> None:
        """Stop all autocalibration (Modbus only; requires ``confirm=True``)."""
        self._portal.call(self._analyzer.stop_calibration, confirm=confirm, timeout=timeout)

    def calibration_status(
        self, group: int = 1, *, timeout: float | None = None
    ) -> CalibrationProgress:
        """Return autocalibration progress for ``group`` (Modbus only)."""
        return self._portal.call(self._analyzer.calibration_status, group, timeout=timeout)

    # ------------------------------------------------------------------ streaming

    def stream(
        self, *, mode: StreamMode | None = None, rate_hz: float | None = None
    ) -> SyncStreamingSession:
        """Return a blocking iterator over the analyser's sample stream."""
        session = self._portal.call(_make_stream, self._analyzer, mode, rate_hz)
        return SyncStreamingSession(self._portal, session)


async def _make_stream(
    analyzer: Analyzer, mode: StreamMode | None, rate_hz: float | None
) -> object:
    # Runs on the loop thread so the anyio memory stream is created in the right context.
    return analyzer.stream(mode=mode, rate_hz=rate_hz)


class Servomex:
    """Entry namespace for the sync facade — :meth:`open` mirrors ``open_device``."""

    open = SyncAnalyzer.open


__all__ = ["Servomex", "SyncAnalyzer", "SyncStreamingSession"]
