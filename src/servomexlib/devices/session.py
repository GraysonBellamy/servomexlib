"""Protocol-neutral session — dispatch point + gate ladder.

The session is the single seam between the :class:`~servomexlib.devices.analyzer.Analyzer`
facade and the active :class:`~servomexlib.protocol.base.ProtocolClient`. Every
call walks three gates, in order, **before any byte is sent**:

1. **Safety-tier** — a ``SafetyTier.STATEFUL`` op (autocalibration) requires
   ``confirm=True`` → else :class:`ServomexConfirmationRequiredError`.
2. **Capability** — the op's :class:`Capability` must be in the active client's
   set → else :class:`ServomexProtocolUnsupportedError` (e.g. ``start_calibration``
   in continuous mode).
3. **Validation** — argument checks (``group ∈ 1..4``, known/populated channel).

It also owns the background-receive lifecycle: a continuous client exposes a
``run`` coroutine that the session hosts in its task group; the Modbus client owns
no background task (it sweeps on demand), so the task group is started only when a
loop is present.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import anyio

from servomexlib.devices.capability import Capability
from servomexlib.errors import (
    ErrorContext,
    ServomexConfirmationRequiredError,
    ServomexProtocolUnsupportedError,
    ServomexValidationError,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from types import TracebackType
    from typing import Self

    from anyio.abc import TaskGroup

    from servomexlib.devices.models import (
        AnalyserStatus,
        CalibrationProgress,
        ChannelStatus,
        DeviceInfo,
        Frame,
        Reading,
    )
    from servomexlib.protocol.base import CalibrationControl, ProtocolClient, ProtocolKind
    from servomexlib.registry.channels import ChannelId

_CAL_GROUPS = (1, 2, 3, 4)


class Session:
    """Owns the active client, the gate ladder, and the background-receive task."""

    def __init__(self, client: ProtocolClient) -> None:
        self._client = client
        self._task_group: TaskGroup | None = None
        self._last_frame: Frame | None = None

    @property
    def client(self) -> ProtocolClient:
        """The active protocol client."""
        return self._client

    @property
    def protocol(self) -> ProtocolKind:
        """The active wire protocol."""
        return self._client.kind

    @property
    def capabilities(self) -> Capability:
        """The active client's capability set."""
        return self._client.capabilities

    # ------------------------------------------------------------------ lifecycle

    async def __aenter__(self) -> Self:
        run = getattr(self._client, "run", None)
        if callable(run):  # continuous client: host the background receive loop
            background = cast("Callable[[], Awaitable[None]]", run)
            task_group = anyio.create_task_group()
            await task_group.__aenter__()
            task_group.start_soon(background)
            self._task_group = task_group
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Cancel the receive loop (if any), then close the client/transport."""
        if self._task_group is not None:
            self._task_group.cancel_scope.cancel()
            await self._task_group.__aexit__(None, None, None)
            self._task_group = None
        await self._client.aclose()

    # ------------------------------------------------------------------ reads

    async def read_frame(self, *, wait_fresh: bool = False, timeout: float | None = None) -> Frame:
        """Read a full frame (gated on ``READ_CHANNELS``); caches it for ``snapshot``."""
        self._require(Capability.READ_CHANNELS)
        frame = await self._client.read_frame(wait_fresh=wait_fresh, timeout=timeout)
        self._last_frame = frame
        return frame

    @property
    def dropped_frames(self) -> int:
        """How many frames the active client has dropped (continuous resync count)."""
        return int(getattr(self._client, "bad_frame_count", 0))

    async def read_channel(self, channel: ChannelId, *, timeout: float | None = None) -> Reading:
        """Read one channel (gated on ``READ_CHANNELS``)."""
        self._require(Capability.READ_CHANNELS)
        return await self._client.read_channel(channel, timeout=timeout)

    async def status(self, channel: ChannelId, *, timeout: float | None = None) -> ChannelStatus:
        """Read one channel's status (gated on ``READ_STATUS``)."""
        self._require(Capability.READ_STATUS)
        return await self._client.status(channel, timeout=timeout)

    async def analyser_status(self, *, timeout: float | None = None) -> AnalyserStatus:
        """Read the analyser-level status (gated on ``READ_STATUS``)."""
        self._require(Capability.READ_STATUS)
        return await self._client.analyser_status(timeout=timeout)

    async def identify(self, *, timeout: float | None = None) -> DeviceInfo:
        """Identify the device (gated on ``IDENTIFY``)."""
        self._require(Capability.IDENTIFY)
        return await self._client.identify(timeout=timeout)

    def snapshot(self) -> Frame:
        """Return the most recent frame known without I/O.

        Prefers the continuous client's background-updated ``latest``; falls back
        to the last frame returned by :meth:`read_frame` (the Modbus path).

        Raises:
            ServomexValidationError: No frame has been observed yet.
        """
        latest = getattr(self._client, "latest", None)
        frame = latest if latest is not None else self._last_frame
        if frame is None:
            raise ServomexValidationError(
                "no frame cached yet; await poll() first",
                context=self._ctx(),
            )
        return frame

    # ------------------------------------------------------------------ control (gated)

    async def start_calibration(
        self, group: int, *, confirm: bool = False, timeout: float | None = None
    ) -> None:
        """Start autocalibration for ``group`` — STATEFUL, gated then dispatched."""
        self._gate_stateful(confirm, op="start_calibration")
        self._require(Capability.AUTOCAL)
        self._validate_group(group)
        await self._control().start_calibration(group, timeout=timeout)

    async def stop_calibration(
        self, *, confirm: bool = False, timeout: float | None = None
    ) -> None:
        """Stop all autocalibration — STATEFUL, gated then dispatched."""
        self._gate_stateful(confirm, op="stop_calibration")
        self._require(Capability.AUTOCAL)
        await self._control().stop_calibration(timeout=timeout)

    async def calibration_status(
        self, group: int = 1, *, timeout: float | None = None
    ) -> CalibrationProgress:
        """Read autocalibration progress (READONLY, gated on ``AUTOCAL``)."""
        self._require(Capability.AUTOCAL)
        self._validate_group(group)
        return await self._control().calibration_status(group, timeout=timeout)

    # ------------------------------------------------------------------ gates

    def _require(self, capability: Capability) -> None:
        if capability not in self._client.capabilities:
            raise ServomexProtocolUnsupportedError(
                f"{self._client.kind.value} mode does not support {capability.name}",
                context=self._ctx(),
            )

    def _gate_stateful(self, confirm: bool, *, op: str) -> None:
        if not confirm:
            raise ServomexConfirmationRequiredError(
                f"{op} is a stateful operation and requires confirm=True",
                context=self._ctx(),
            )

    def _validate_group(self, group: int) -> None:
        if group not in _CAL_GROUPS:
            raise ServomexValidationError(
                f"cal-group must be 1-4 (got {group})",
                context=self._ctx(),
            )

    def _control(self) -> CalibrationControl:
        # The capability gate has already confirmed AUTOCAL, so the client is a
        # CalibrationControl; the cast bridges the read-only ProtocolClient type.
        return cast("CalibrationControl", self._client)

    def _ctx(self) -> ErrorContext:
        return ErrorContext(protocol=self._client.kind)


__all__ = ["Session"]
