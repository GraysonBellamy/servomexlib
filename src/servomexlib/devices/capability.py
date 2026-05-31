"""Capability / safety-tier / availability enums.

The per-protocol client advertises a :class:`Capability` set; the session
consults it to gate operations so the facade exposes one uniform method set
and unsupported operations fail cleanly per mode.
"""

from __future__ import annotations

from enum import Flag, StrEnum, auto


class Capability(Flag):
    """What a :class:`ProtocolClient` can do.

    A small :class:`enum.Flag`: the continuous client advertises read/identify
    only; the Modbus client advertises everything. The session gates each op
    against the active client's set.
    """

    READ_CHANNELS = auto()
    READ_STATUS = auto()
    IDENTIFY = auto()
    AUTOCAL = auto()
    LOOPBACK = auto()


class SafetyTier(StrEnum):
    """How disruptive an operation is.

    ``STATEFUL`` operations (autocalibration) require ``confirm=True`` and are
    gated *before any byte is sent*.
    """

    READONLY = "readonly"
    STATEFUL = "stateful"


class Availability(StrEnum):
    """Whether a capability is known-present, known-absent, or unprobed."""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


__all__ = ["Availability", "Capability", "SafetyTier"]
