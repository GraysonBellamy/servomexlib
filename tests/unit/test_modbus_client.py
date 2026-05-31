"""ModbusClient over the byte-accurate MockSlave fake, both framings."""

from __future__ import annotations

import math

import anymodbus
import pytest

from servomexlib.errors import ServomexIllegalFunctionError
from servomexlib.protocol.base import CalibrationControl, ProtocolClient, ProtocolKind
from servomexlib.protocol.modbus import registers as reg
from servomexlib.registry.channels import ChannelId
from servomexlib.registry.units import Unit
from servomexlib.testing import DEFAULT_4100_BANK, MockChannel, mock_modbus_pair, read_ops
from tests.conftest import approx

pytestmark = pytest.mark.anyio

_FRAMINGS = [
    pytest.param(anymodbus.Framing.RTU, id="rtu"),
    pytest.param(anymodbus.Framing.ASCII, id="ascii"),
]
_FIVE = (ChannelId.I1, ChannelId.I2, ChannelId.I3, ChannelId.E1, ChannelId.E2)


@pytest.mark.parametrize("framing", _FRAMINGS)
async def test_read_frame_floats_cross_check_continuous_capture(framing: anymodbus.Framing) -> None:
    # FC04 floats must decode to the same values the continuous capture shows.
    async with mock_modbus_pair(framing=framing, client_channels=_FIVE) as (client, _slave):
        frame = await client.read_frame()
    values = {r.channel: r.value for r in frame.readings}
    assert values[ChannelId.I1] == approx(20.378, abs=1e-3)
    assert values[ChannelId.I2] == approx(0.084, abs=1e-3)
    assert values[ChannelId.I3] == approx(0.250, abs=1e-3)
    assert values[ChannelId.E1] == approx(0.0)


@pytest.mark.parametrize("framing", _FRAMINGS)
async def test_charset_and_unit_decode(framing: anymodbus.Framing) -> None:
    async with mock_modbus_pair(framing=framing, client_channels=_FIVE) as (client, _slave):
        i1 = await client.read_channel(ChannelId.I1)
        i3 = await client.read_channel(ChannelId.I3)
        e1 = await client.read_channel(ChannelId.E1)
    assert i1.name == "Oxygen"
    assert i1.unit is Unit.PERCENT
    assert i3.name == "CO₂"  # display-ROM 0x82 → subscript-2, NUL-stripped
    assert e1.name is None  # unpopulated slot
    assert e1.unit is Unit.MILLIAMP


async def test_protocol_kind_follows_framing() -> None:
    async with mock_modbus_pair(framing=anymodbus.Framing.RTU) as (client, _slave):
        assert client.kind is ProtocolKind.MODBUS_RTU
    async with mock_modbus_pair(framing=anymodbus.Framing.ASCII) as (client, _slave):
        assert client.kind is ProtocolKind.MODBUS_ASCII


async def test_status_decode_external_invalid_and_transducer_fault() -> None:
    bank = (
        MockChannel(
            ChannelId.I1,
            20.378,
            name=b"Oxygen",
            unit=b"%\x00\x00",
            status_bits=(True, False, False, False, False, False, False, False),
        ),
        MockChannel(
            ChannelId.E1,
            0.0,
            unit=b"mA\x00",
            status_bits=(True, False, False, False, False, False, False, False),
        ),
    )
    async with mock_modbus_pair(channels=bank, client_channels=(ChannelId.I1, ChannelId.E1)) as (
        client,
        _slave,
    ):
        i1 = await client.status(ChannelId.I1)
        e1 = await client.status(ChannelId.E1)
    assert i1.fault  # transducer bit 0 = Fault
    assert not e1.ok  # external bit 0 = Invalid → surfaced as not-ok
    assert not e1.maintenance  # reserved bits forced clear, not read as cal/maint


async def test_nan_value_decodes_to_none() -> None:
    bank = (MockChannel(ChannelId.I1, math.nan, name=b"Oxygen", unit=b"%\x00\x00"),)
    async with mock_modbus_pair(channels=bank, client_channels=(ChannelId.I1,)) as (
        client,
        _slave,
    ):
        frame = await client.read_frame()

    assert frame.channel(ChannelId.I1).value is None


async def test_identify_reports_only_populated_slots() -> None:
    async with mock_modbus_pair(client_channels=tuple(ChannelId)) as (client, _slave):
        info = await client.identify()
    populated = {c.channel for c in info.channels}
    # Default bank populates I1/I2/I3 (named) + E1/E2 are unlabelled → excluded.
    assert ChannelId.I1 in populated
    assert ChannelId.I3 in populated
    assert ChannelId.E1 not in populated
    assert info.protocol is ProtocolKind.MODBUS_RTU
    assert info.address == 1


async def test_identify_caches_static_metadata_until_refresh() -> None:
    async with mock_modbus_pair(client_channels=_FIVE) as (client, slave):
        await client.identify()
        _write_words(slave.input_registers, reg.name_pdu(ChannelId.I1), b"Drift ")
        _write_words(slave.input_registers, reg.unit_pdu(ChannelId.I1), b"mA\x00")

        cached = await client.read_channel(ChannelId.I1)
        refreshed = await client.identify()

    assert cached.name == "Oxygen"
    assert cached.unit is Unit.PERCENT
    refreshed_i1 = next(ch for ch in refreshed.channels if ch.channel is ChannelId.I1)
    assert refreshed_i1.name == "Drift"
    assert refreshed_i1.unit is Unit.MILLIAMP


async def test_read_channel_still_reads_slot_excluded_by_identify() -> None:
    async with mock_modbus_pair(client_channels=tuple(ChannelId)) as (client, _slave):
        info = await client.identify()
        e1 = await client.read_channel(ChannelId.E1)

    assert ChannelId.E1 not in {ch.channel for ch in info.channels}
    assert e1.channel is ChannelId.E1
    assert e1.name is None
    assert e1.unit is Unit.MILLIAMP


async def test_coalesced_frame_uses_minimal_common_path_transactions() -> None:
    async with mock_modbus_pair(client_channels=_FIVE, record_read_ops=True) as (client, slave):
        await client.read_frame()

    reads = [(op.function_code, op.address, op.count) for op in read_ops(slave)]
    assert reads == [
        (0x04, 0, 70),
        (0x02, 0, 80),
        (0x02, 1000, 16),
    ]


async def test_rejected_broad_span_falls_back_and_learns_strict_policy() -> None:
    async with mock_modbus_pair(
        client_channels=_FIVE,
        record_read_ops=True,
        valid_input_ranges=((0, 21), (56, 70)),
        valid_discrete_ranges=((0, 24), (64, 80), (1000, 1016)),
    ) as (client, slave):
        first = await client.read_frame()
        first_read_count = len(read_ops(slave))
        second = await client.read_frame()
        second_reads = read_ops(slave)[first_read_count:]

    assert first.channel(ChannelId.I1).value == approx(20.378, abs=1e-3)
    assert second.channel(ChannelId.E1).unit is Unit.MILLIAMP
    assert client.read_policy.is_strict
    assert [(op.function_code, op.address, op.count) for op in second_reads] == [
        (0x04, 0, 21),
        (0x04, 56, 14),
        (0x02, 0, 24),
        (0x02, 64, 16),
        (0x02, 1000, 16),
    ]


async def test_loopback_echoes_payload() -> None:
    async with mock_modbus_pair() as (client, _slave):
        assert await client.loopback(b"\xab\xcd") == b"\xab\xcd"


async def test_disabled_fc_maps_to_illegal_function() -> None:
    # FC04 disabled → reading raises an anymodbus IllegalFunction → mapped.
    async with mock_modbus_pair(
        client_channels=_FIVE, disabled_function_codes=frozenset({0x04})
    ) as (client, _slave):
        with pytest.raises(ServomexIllegalFunctionError):
            await client.read_channel(ChannelId.I1)


async def test_clients_satisfy_protocols() -> None:
    async with mock_modbus_pair() as (client, _slave):
        assert isinstance(client, ProtocolClient)
        assert isinstance(client, CalibrationControl)


async def test_default_bank_constant_is_five_channels() -> None:
    assert len(DEFAULT_4100_BANK) == 5


def _write_words(bank: list[int], address: int, data: bytes) -> None:
    padded = data + b"\x00" * (-len(data) % 2)
    for offset in range(0, len(padded), 2):
        bank[address + offset // 2] = int.from_bytes(padded[offset : offset + 2], "big")
