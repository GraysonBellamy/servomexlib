"""Public frozen dataclasses returned by the :class:`Analyzer` facade.

All types are immutable (``frozen=True, slots=True``) so they are safe to share,
pass across task boundaries, and log. Every protocol decodes to the **same**
models — that is the whole point of the protocol-neutral API.

Timestamp contract: ``received_at`` (wall-clock UTC at acquisition)
plus ``monotonic_ns`` (the streaming join key) ride on ``Reading`` / ``Frame`` /
``Sample``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from servomexlib.errors import ServomexValidationError

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime

    from servomexlib.protocol.base import ProtocolKind
    from servomexlib.registry.channels import ChannelId, ChannelKind
    from servomexlib.registry.units import Unit
    from servomexlib.transport.base import SerialSettings


__all__ = [
    "AnalyserStatus",
    "CalGroupState",
    "CalPhase",
    "CalibrationProgress",
    "ChannelInfo",
    "ChannelStatus",
    "DeviceInfo",
    "Frame",
    "Reading",
    "Sample",
]


class CalPhase(StrEnum):
    """The phase of an autocalibration cycle for one cal-group."""

    IDLE = "idle"
    SAMPLING = "sampling"
    CAL_GAS_1 = "cal_gas_1"
    CAL_GAS_2 = "cal_gas_2"


@dataclass(frozen=True, slots=True)
class CalGroupState:
    """One cal-group's autocalibration state.

    Decoded from the continuous-frame autocal field (``S``/``C`` + ``1``/``2``
    per group) or the Modbus analyser-status cal-group discretes.
    """

    group: int
    calibrating: bool
    cal_gas: int  # 1 or 2 — which calibration gas the group is configured for


@dataclass(frozen=True, slots=True)
class ChannelStatus:
    """Per-channel status flags."""

    fault: bool
    maintenance: bool
    calibrating: bool
    warming_up: bool
    alarms: tuple[bool, bool, bool, bool]

    @property
    def ok(self) -> bool:
        """``True`` when the channel reports no fault and no active alarm."""
        return not self.fault and not any(self.alarms)


@dataclass(frozen=True, slots=True)
class Reading:
    """One decoded channel reading.

    ``value`` is ``None`` on over-range / invalid; ``name`` is ``None`` when the
    slot is unlabelled (continuous ``||||||``). ``name`` and ``unit`` have been
    routed through the display charset.
    """

    channel: ChannelId
    kind: ChannelKind
    name: str | None
    value: float | None
    unit: Unit
    status: ChannelStatus
    protocol: ProtocolKind
    received_at: datetime
    monotonic_ns: int
    raw: bytes

    def as_dict(self) -> dict[str, float | int | str | bool | None]:
        """Flatten the reading into a row-shaped dict for tabular sinks.

        Content-only — timing provenance lives on the surrounding
        :class:`Sample`. Booleans render as ``0`` / ``1`` so SQLite picks
        INTEGER affinity and CSV / JSONL round-trip cleanly.
        """
        return {
            "channel": self.channel.value,
            "kind": self.kind.value,
            "name": self.name,
            "value": self.value,
            "unit": self.unit.value,
            "fault": int(self.status.fault),
            "maintenance": int(self.status.maintenance),
            "calibrating": int(self.status.calibrating),
            "warming_up": int(self.status.warming_up),
            "alarm1": int(self.status.alarms[0]),
            "alarm2": int(self.status.alarms[1]),
            "alarm3": int(self.status.alarms[2]),
            "alarm4": int(self.status.alarms[3]),
            "ok": int(self.status.ok),
            "protocol": self.protocol.value,
        }

    def __format__(self, format_spec: str) -> str:
        """Delegate non-empty format specs to :attr:`value`.

        ``f"{r:.4f}"`` formats the value; ``f"{r}"`` prints the dataclass repr.
        An over-range reading (``value`` is ``None``) formats as ``"None"`` for
        any numeric spec rather than raising.
        """
        if format_spec == "":
            return str(self)
        if self.value is None:
            return "None"
        return format(self.value, format_spec)


@dataclass(frozen=True, slots=True)
class AnalyserStatus:
    """Analyser-level status."""

    fault: bool
    maintenance: bool
    cal_groups: tuple[CalGroupState, ...]
    clock: datetime | None  # the analyser's own date/time (may be unset/wrong)


@dataclass(frozen=True, slots=True)
class Frame:
    """One continuous frame, or one Modbus sweep — a timestamped channel set."""

    readings: tuple[Reading, ...]
    analyser: AnalyserStatus
    protocol: ProtocolKind
    received_at: datetime
    monotonic_ns: int
    raw: bytes

    def channel(self, cid: ChannelId) -> Reading:
        """Return the :class:`Reading` for ``cid``.

        Raises:
            ServomexValidationError: ``cid`` is not present in this frame.
        """
        for reading in self.readings:
            if reading.channel == cid:
                return reading
        raise ServomexValidationError(
            f"channel {getattr(cid, 'value', cid)} not present in frame",
        )

    def as_samples(self, *, device: str = "") -> list[Sample]:
        """Fan the frame out into one long-format :class:`Sample` per reading."""
        return [
            Sample(
                device=device,
                channel=reading.channel,
                reading=reading,
                protocol=self.protocol,
                monotonic_ns=self.monotonic_ns,
                received_at=self.received_at,
            )
            for reading in self.readings
        ]


@dataclass(frozen=True, slots=True)
class ChannelInfo:
    """Identity of one populated channel slot (part of :class:`DeviceInfo`)."""

    channel: ChannelId
    kind: ChannelKind
    name: str | None
    unit: Unit


@dataclass(frozen=True, slots=True)
class DeviceInfo:
    """Identity snapshot produced by ``Analyzer.identify``."""

    model: str
    channels: tuple[ChannelInfo, ...]
    protocol: ProtocolKind
    address: int
    serial_settings: SerialSettings


@dataclass(frozen=True, slots=True)
class CalibrationProgress:
    """Autocalibration progress for one cal-group."""

    group: int
    active: bool
    phase: CalPhase


def _empty_metadata() -> Mapping[str, object]:
    return {}


@dataclass(frozen=True, slots=True)
class Sample:
    """Long-format row — one channel reading with streaming provenance.

    ``requested_at`` / ``latency_s`` are ``None`` in passive continuous mode (we
    did not ask). ``error`` is set when a frame was dropped/corrupt; ``reading``
    is then ``None`` (the two are mutually exclusive) and ``channel`` is ``None``
    because a dropped frame is not tied to one channel.
    """

    device: str
    channel: ChannelId | None
    reading: Reading | None
    protocol: ProtocolKind
    monotonic_ns: int  # join key (timestamp contract)
    received_at: datetime  # wall-clock UTC at acquisition
    requested_at: datetime | None = None  # None in passive continuous mode
    latency_s: float | None = None  # None in passive mode (no request to measure)
    metadata: Mapping[str, object] = field(default_factory=_empty_metadata)
    error: BaseException | None = None  # set when a frame was dropped/corrupt
