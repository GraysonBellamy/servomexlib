"""servomexlib — async Python driver for Servomex SERVOPRO 4000-series gas analysers.

One protocol-neutral, channel-oriented API decodes the analyser's three
communication modes — **Continuous ASCII** (unsolicited broadcast),
**Modbus RTU**, and **Modbus ASCII** — into a single set of typed models.

The public API is semantic and protocol-neutral: a caller asks for
``poll()``, ``read_channel("I1")``, ``snapshot()``, ``identify()``,
``stream()``, ``start_calibration(group)`` and the session dispatches the
right per-protocol client selected (or sniffed via ``AUTO``) at open time.

The core is ``async`` (built on ``anyio``); a thin sync facade lives at
:mod:`servomexlib.sync` for scripts, notebooks, and REPL use.

``servomexlib`` is a member of the ``*lib`` instrument-driver family; family
harmony is defined at the boundary (entry point, frozen models, error
hierarchy, streaming/sinks/sync/CLI conventions, tooling).

This module re-exports the public surface.
"""

from __future__ import annotations

from servomexlib.devices.analyzer import Analyzer
from servomexlib.devices.capability import Availability, Capability, SafetyTier
from servomexlib.devices.factory import open_continuous, open_device
from servomexlib.devices.models import (
    AnalyserStatus,
    CalGroupState,
    CalibrationProgress,
    CalPhase,
    ChannelInfo,
    ChannelStatus,
    DeviceInfo,
    Frame,
    Reading,
    Sample,
)
from servomexlib.errors import (
    ErrorContext,
    ServomexCapabilityError,
    ServomexChecksumError,
    ServomexConfigurationError,
    ServomexConfirmationRequiredError,
    ServomexConnectionError,
    ServomexError,
    ServomexFrameError,
    ServomexModbusError,
    ServomexParseError,
    ServomexProtocolError,
    ServomexProtocolUnsupportedError,
    ServomexTimeoutError,
    ServomexTransportError,
    ServomexValidationError,
)
from servomexlib.manager import DeviceResult, ErrorPolicy, ServomexManager
from servomexlib.protocol.base import ProtocolKind
from servomexlib.registry.channels import ChannelId, ChannelKind
from servomexlib.registry.units import Unit, UnitKind
from servomexlib.streaming import (
    AcquisitionSummary,
    OverflowPolicy,
    PollSource,
    Recording,
    StreamingSession,
    StreamMode,
    record,
)
from servomexlib.transport.base import SerialSettings, Transport
from servomexlib.transport.fake import FakeTransport
from servomexlib.version import __version__

__all__ = [
    "AcquisitionSummary",
    "AnalyserStatus",
    "Analyzer",
    "Availability",
    "CalGroupState",
    "CalPhase",
    "CalibrationProgress",
    "Capability",
    "ChannelId",
    "ChannelInfo",
    "ChannelKind",
    "ChannelStatus",
    "DeviceInfo",
    "DeviceResult",
    "ErrorContext",
    "ErrorPolicy",
    "FakeTransport",
    "Frame",
    "OverflowPolicy",
    "PollSource",
    "ProtocolKind",
    "Reading",
    "Recording",
    "SafetyTier",
    "Sample",
    "SerialSettings",
    "ServomexCapabilityError",
    "ServomexChecksumError",
    "ServomexConfigurationError",
    "ServomexConfirmationRequiredError",
    "ServomexConnectionError",
    "ServomexError",
    "ServomexFrameError",
    "ServomexManager",
    "ServomexModbusError",
    "ServomexParseError",
    "ServomexProtocolError",
    "ServomexProtocolUnsupportedError",
    "ServomexTimeoutError",
    "ServomexTransportError",
    "ServomexValidationError",
    "StreamMode",
    "StreamingSession",
    "Transport",
    "Unit",
    "UnitKind",
    "__version__",
    "open_continuous",
    "open_device",
    "record",
]
