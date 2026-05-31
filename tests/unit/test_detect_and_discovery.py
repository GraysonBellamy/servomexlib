"""The AUTO sniff ladder + discovery."""

from __future__ import annotations

from typing import TypedDict

import anyio
import anymodbus
import pytest

from servomexlib.devices.discovery import discover_port, find_devices, summarize
from servomexlib.errors import ServomexConnectionError
from servomexlib.protocol.base import ProtocolKind
from servomexlib.protocol.detect import detect_protocol
from servomexlib.testing import FakeTransport, mock_modbus_transport, split_continuous_frames

pytestmark = pytest.mark.anyio


class _FastProbe(TypedDict):
    """Fast AUTO-ladder knobs for tests; typed so ``**_FAST`` keeps per-key types."""

    probe_timeout: float
    probe_tries: int
    listen_timeout: float


_FAST: _FastProbe = {"probe_timeout": 0.2, "probe_tries": 1, "listen_timeout": 1.0}


@pytest.mark.parametrize(
    ("framing", "expected"),
    [
        (anymodbus.Framing.RTU, ProtocolKind.MODBUS_RTU),
        (anymodbus.Framing.ASCII, ProtocolKind.MODBUS_ASCII),
    ],
)
async def test_auto_resolves_modbus_framing(
    framing: anymodbus.Framing, expected: ProtocolKind
) -> None:
    async with mock_modbus_transport(framing=framing) as (transport, _slave):
        assert await detect_protocol(transport, address=1, **_FAST) is expected


async def test_auto_resolves_continuous(continuous_capture: bytes) -> None:
    frame = split_continuous_frames(continuous_capture)[0] + b"\r\n"
    fake = FakeTransport()

    async def feed_continuously() -> None:
        # Mimic a live broadcaster: the Modbus probes drain/consume early bytes,
        # so keep emitting whole frames for the passive-listen phase to resync on.
        for _ in range(200):
            fake.feed(frame)
            await anyio.sleep(0.01)

    async with anyio.create_task_group() as tg:
        tg.start_soon(feed_continuously)
        kind = await detect_protocol(
            fake, address=1, probe_timeout=0.2, probe_tries=1, listen_timeout=2.0
        )
        assert kind is ProtocolKind.CONTINUOUS_ASCII
        tg.cancel_scope.cancel()


async def test_auto_silent_transport_raises() -> None:
    fake = FakeTransport()  # never feeds anything, no modbus reply
    with pytest.raises(ServomexConnectionError, match="no recognised protocol"):
        await detect_protocol(fake, address=1, **_FAST)


async def test_pushback_drained_before_modbus_framer() -> None:
    # A continuous fragment parked in the buffer must not leak into the framer:
    # detect drains before probing, so a Modbus device is still recognised.
    async with mock_modbus_transport(framing=anymodbus.Framing.RTU) as (transport, _slave):
        transport.pushback(b" 06-10-20;garbage")  # stray continuous bytes
        assert await detect_protocol(transport, address=1, **_FAST) is ProtocolKind.MODBUS_RTU


async def test_discover_modbus_port() -> None:
    async with mock_modbus_transport() as (transport, _slave):
        result = await discover_port(transport, timeout=0.5)
    assert result.ok
    assert result.protocol is ProtocolKind.MODBUS_RTU
    assert not result.is_broadcaster
    assert result.info is not None


async def test_find_devices_folds_summary() -> None:
    async with mock_modbus_transport() as (modbus_transport, _slave):
        silent = FakeTransport(label="fake://silent")
        results = await find_devices([modbus_transport, silent], timeout=0.4)
    summary = summarize(results)
    assert summary.total == 2
    assert len(summary.found) == 1
    assert len(summary.failed) == 1
    assert summary.found[0].protocol is ProtocolKind.MODBUS_RTU
