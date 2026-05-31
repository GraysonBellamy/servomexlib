"""open_device across protocols + the session gate ladder."""

from __future__ import annotations

import pytest

from servomexlib import open_device
from servomexlib.errors import (
    ServomexConfirmationRequiredError,
    ServomexProtocolUnsupportedError,
    ServomexValidationError,
)
from servomexlib.protocol.base import ProtocolKind
from servomexlib.registry.channels import ChannelId
from servomexlib.streaming import StreamMode
from servomexlib.testing import FakeTransport, mock_modbus_transport, split_continuous_frames

pytestmark = pytest.mark.anyio

_FIVE = (ChannelId.I1, ChannelId.I2, ChannelId.I3, ChannelId.E1, ChannelId.E2)


def _frames(capture: bytes) -> list[bytes]:
    return [frame + b"\r\n" for frame in split_continuous_frames(capture)]


# --- explicit-protocol open_device ---------------------------------------


async def test_open_device_continuous_explicit(continuous_capture: bytes) -> None:
    fake = FakeTransport()
    anz = await open_device(fake, protocol=ProtocolKind.CONTINUOUS_ASCII, identify=False)
    async with anz:
        fake.feed(_frames(continuous_capture)[0])
        frame = await anz.poll()
    assert anz.protocol is ProtocolKind.CONTINUOUS_ASCII
    assert frame.channel(ChannelId.I1).name == "Oxygen"


async def test_open_device_modbus_explicit_and_identify_caches() -> None:
    async with mock_modbus_transport() as (transport, _slave):
        anz = await open_device(
            transport, protocol=ProtocolKind.MODBUS_RTU, address=1, identify=True
        )
        async with anz:
            assert anz.protocol is ProtocolKind.MODBUS_RTU
            assert anz.info is not None  # cached on __aenter__
            assert ChannelId.I1 in {c.channel for c in anz.info.channels}


# --- gate ladder ----------------------------------------------------------


async def test_start_calibration_without_confirm_raises() -> None:
    async with mock_modbus_transport() as (transport, _slave):
        async with await open_device(
            transport, protocol=ProtocolKind.MODBUS_RTU, identify=False
        ) as anz:
            with pytest.raises(ServomexConfirmationRequiredError):
                await anz.start_calibration(1)


async def test_start_calibration_in_continuous_is_unsupported() -> None:
    fake = FakeTransport()
    async with await open_device(
        fake, protocol=ProtocolKind.CONTINUOUS_ASCII, identify=False
    ) as anz:
        # confirm=True passes the safety gate; the capability gate then refuses it.
        with pytest.raises(ServomexProtocolUnsupportedError):
            await anz.start_calibration(1, confirm=True)


async def test_bad_group_validates_after_gates() -> None:
    async with mock_modbus_transport() as (transport, _slave):
        async with await open_device(
            transport, protocol=ProtocolKind.MODBUS_RTU, identify=False
        ) as anz:
            with pytest.raises(ServomexValidationError):
                await anz.start_calibration(7, confirm=True)


async def test_unknown_channel_validates() -> None:
    fake = FakeTransport()
    async with await open_device(
        fake, protocol=ProtocolKind.CONTINUOUS_ASCII, identify=False
    ) as anz:
        with pytest.raises(ServomexValidationError):
            await anz.read_channel("Z9")


async def test_modbus_calibration_status_works() -> None:
    async with mock_modbus_transport() as (transport, _slave):
        async with await open_device(
            transport, protocol=ProtocolKind.MODBUS_RTU, identify=False
        ) as anz:
            prog = await anz.calibration_status(1)
            assert prog.group == 1
            assert not prog.active  # idle bank


async def test_modbus_poll_streaming_yields_samples() -> None:
    async with mock_modbus_transport() as (transport, _slave):
        async with await open_device(
            transport, protocol=ProtocolKind.MODBUS_RTU, identify=False
        ) as anz:
            async with anz.stream(rate_hz=20) as samples:
                first = await samples.__anext__()
            assert first.reading is not None
            assert first.channel in _FIVE


async def test_modbus_stream_rejects_autoprint() -> None:
    async with mock_modbus_transport() as (transport, _slave):
        async with await open_device(
            transport, protocol=ProtocolKind.MODBUS_RTU, identify=False
        ) as anz:
            with pytest.raises(ServomexValidationError, match="POLL"):
                anz.stream(mode=StreamMode.AUTOPRINT)
