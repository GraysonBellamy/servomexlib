"""The :class:`ProtocolKind` enum and the semantic client interfaces.

``ProtocolKind`` is named with the ``Kind`` suffix to avoid colliding with
:class:`typing.Protocol` at import sites.

:class:`ProtocolClient` is the small *semantic* surface every mode implements —
the analyser's actual read capabilities, not a generic ``execute(bytes)`` layer.
The autocalibration control surface lives on a separate
:class:`CalibrationControl` protocol because it is present **only** when
``AUTOCAL`` is in the client's :attr:`~ProtocolClient.capabilities` (Modbus); the
session's capability gate refuses it for continuous mode *before*
any dispatch, so the continuous client never needs to implement it.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
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


class ProtocolKind(StrEnum):
    """Which communication mode the analyser speaks.

    All three modes are mutually exclusive on the wire and selected on the
    analyser's front panel. ``AUTO`` is only valid at ``open_device`` call
    time; by the time a session exists it has resolved to one of the others.
    """

    AUTO = "auto"
    CONTINUOUS_ASCII = "continuous"
    MODBUS_RTU = "modbus_rtu"
    MODBUS_ASCII = "modbus_ascii"


@runtime_checkable
class ProtocolClient(Protocol):
    """The uniform read/identify surface every per-protocol client implements.

    The session dispatches the facade's reads against this interface; the
    concrete clients (:class:`~servomexlib.protocol.continuous.client.ContinuousClient`,
    :class:`~servomexlib.protocol.modbus.client.ModbusClient`) decode their wire
    formats into the **same** models.
    """

    @property
    def kind(self) -> ProtocolKind: ...
    @property
    def capabilities(self) -> Capability: ...

    async def read_frame(
        self, *, wait_fresh: bool = False, timeout: float | None = None
    ) -> Frame: ...
    async def read_channel(
        self, channel: ChannelId, *, timeout: float | None = None
    ) -> Reading: ...
    async def status(
        self, channel: ChannelId, *, timeout: float | None = None
    ) -> ChannelStatus: ...
    async def analyser_status(self, *, timeout: float | None = None) -> AnalyserStatus: ...
    async def identify(self, *, timeout: float | None = None) -> DeviceInfo: ...
    async def aclose(self) -> None: ...


@runtime_checkable
class CalibrationControl(Protocol):
    """The autocalibration control surface — present only when ``AUTOCAL`` is advertised.

    Implemented by the Modbus client; the session casts to this protocol once the
    capability gate confirms ``AUTOCAL`` is supported. These
    methods are pure I/O — the ``confirm`` safety gate and the capability gate
    live in the session, *before* dispatch.
    """

    async def start_calibration(self, group: int, *, timeout: float | None = None) -> None: ...
    async def stop_calibration(self, *, timeout: float | None = None) -> None: ...
    async def calibration_status(
        self, group: int = 1, *, timeout: float | None = None
    ) -> CalibrationProgress: ...


__all__ = ["CalibrationControl", "ProtocolClient", "ProtocolKind"]
