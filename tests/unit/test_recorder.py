"""The drift-free POLL recorder.

Driven against a lightweight in-process :class:`PollSource` stub so scheduling,
overflow policy, and the live :class:`AcquisitionSummary` are tested without a
transport. One end-to-end test drives the real Modbus client via ``mock_modbus_pair``.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

import anyio
import pytest

from servomexlib.devices.models import Reading
from servomexlib.errors import ServomexConnectionError
from servomexlib.protocol.base import ProtocolKind
from servomexlib.registry.channels import ChannelId, ChannelKind
from servomexlib.registry.status import decode_discrete_status
from servomexlib.registry.units import Unit
from servomexlib.streaming import OverflowPolicy, Sample, record
from servomexlib.testing import mock_modbus_pair

pytestmark = pytest.mark.anyio


def _sample(seq: int) -> Sample:
    reading = Reading(
        channel=ChannelId.I1,
        kind=ChannelKind.TRANSDUCER,
        name="Oxygen",
        value=float(seq),
        unit=Unit.PERCENT,
        status=decode_discrete_status((False,) * 8, ChannelKind.TRANSDUCER),
        protocol=ProtocolKind.MODBUS_RTU,
        received_at=datetime.now(UTC),
        monotonic_ns=time.monotonic_ns(),
        raw=b"",
    )
    return Sample(
        device="stub",
        channel=ChannelId.I1,
        reading=reading,
        protocol=ProtocolKind.MODBUS_RTU,
        monotonic_ns=reading.monotonic_ns,
        received_at=reading.received_at,
    )


class _StubSource:
    """A counting PollSource: each tick returns one incrementing sample."""

    def __init__(self, *, fail_first: int = 0) -> None:
        self.calls = 0
        self._fail_first = fail_first

    async def poll_samples(
        self, *, names: object = None, timeout: float | None = None
    ) -> list[Sample]:
        del names, timeout
        self.calls += 1
        if self.calls <= self._fail_first:
            raise ServomexConnectionError("transient drop")
        return [_sample(self.calls)]


async def test_record_emits_for_duration() -> None:
    source = _StubSource()
    async with record(source, rate_hz=50, duration=0.2) as recording:
        batches = [batch async for batch in recording.stream]
    assert batches  # at least one tick
    assert all(len(b) == 1 for b in batches)
    assert recording.summary.finished_at is not None
    assert recording.summary.samples_emitted == len(batches)


async def test_record_summary_counts_emitted() -> None:
    source = _StubSource()
    async with record(source, rate_hz=100, duration=0.1) as recording:
        received = [batch async for batch in recording.stream]
    assert recording.summary.samples_emitted == len(received)
    assert recording.summary.max_drift_ms >= 0.0


async def test_record_rejects_bad_rate() -> None:
    source = _StubSource()
    with pytest.raises(ValueError, match="rate_hz"):
        async with record(source, rate_hz=0):
            pass


async def test_record_drop_newest_counts_late() -> None:
    source = _StubSource()
    # buffer_size=1, slow consumer: producer outruns it and drops newest batches.
    async with record(
        source, rate_hz=200, duration=0.2, overflow=OverflowPolicy.DROP_NEWEST, buffer_size=1
    ) as recording:
        await anyio.sleep(0.25)  # let the producer overrun the unread buffer
        drained = [batch async for batch in recording.stream]
    assert recording.summary.samples_late >= 1
    assert len(drained) <= recording.summary.samples_emitted + 1


async def test_record_auto_reconnect_absorbs_connection_error() -> None:
    source = _StubSource(fail_first=1)
    async with record(source, rate_hz=50, duration=0.3, auto_reconnect=True) as recording:
        batches = [batch async for batch in recording.stream]
    assert recording.summary.disconnects >= 1
    assert batches  # recovered and produced after the transient failure


async def test_record_propagates_connection_error_without_reconnect() -> None:
    source = _StubSource(fail_first=1)

    async def _drain() -> None:
        async with record(source, rate_hz=50, duration=0.3) as recording:
            async for _batch in recording.stream:
                pass

    with pytest.raises(ServomexConnectionError):
        await _drain()


async def test_record_end_to_end_over_modbus() -> None:
    async with mock_modbus_pair() as (client, _slave):
        from servomexlib.devices.analyzer import Analyzer

        async with Analyzer(client, device="mock") as anz:
            async with record(anz, rate_hz=50, duration=0.1) as recording:
                batches = [batch async for batch in recording.stream]
    assert batches
    channels = {s.channel for batch in batches for s in batch}
    assert ChannelId.I1 in channels
