"""Streaming layer.

Two acquisition shapes share one :class:`Sample` model and :class:`StreamingSession`
interface: the passive ``AUTOPRINT`` subscribe (continuous broadcast) and the
drift-free ``POLL`` :func:`record` loop (Modbus). :class:`PollSource` is the narrow
contract the recorder drives.
"""

from __future__ import annotations

from servomexlib.streaming.poll_source import PollSource
from servomexlib.streaming.recorder import (
    AcquisitionSummary,
    OverflowPolicy,
    Recording,
    record,
)
from servomexlib.streaming.sample import Sample
from servomexlib.streaming.stream_session import StreamingSession, StreamMode

__all__ = [
    "AcquisitionSummary",
    "OverflowPolicy",
    "PollSource",
    "Recording",
    "Sample",
    "StreamMode",
    "StreamingSession",
    "record",
]
