"""The generated channel addressing table matches the HW-confirmed map."""

from __future__ import annotations

import pytest

from servomexlib.errors import ServomexConfigurationError
from servomexlib.registry import channels
from servomexlib.registry.channels import (
    CHANNEL_SPECS,
    ChannelId,
    ChannelKind,
    ChannelSpec,
    spec_for,
)


def test_all_ten_slots_present() -> None:
    assert set(CHANNEL_SPECS) == set(ChannelId)
    assert len(CHANNEL_SPECS) == 10


@pytest.mark.parametrize(
    ("channel", "value", "name", "unit", "discrete"),
    [
        # HW-confirmed I-block (stride 7 regs / 8 discretes).
        (ChannelId.I1, 30001, 30003, 30006, 10001),
        (ChannelId.I2, 30008, 30010, 30013, 10009),
        (ChannelId.I3, 30015, 30017, 30020, 10017),
        (ChannelId.I4, 30022, 30024, 30027, 10025),
        # D-block from 30029 / 10033.
        (ChannelId.D1, 30029, 30031, 30034, 10033),
        (ChannelId.D4, 30050, 30052, 30055, 10057),
        # E-block from 30057 / 10065.
        (ChannelId.E1, 30057, 30059, 30062, 10065),
        (ChannelId.E2, 30064, 30066, 30069, 10073),
    ],
)
def test_addresses_match_confirmed_map(
    channel: ChannelId, value: int, name: int, unit: int, discrete: int
) -> None:
    spec = spec_for(channel)
    assert spec.value_register == value
    assert spec.name_register == name
    assert spec.unit_register == unit
    assert spec.status_discrete == discrete


def test_kind_classification() -> None:
    assert spec_for(ChannelId.I1).kind is ChannelKind.TRANSDUCER
    assert spec_for(ChannelId.D1).kind is ChannelKind.DERIVED
    assert spec_for(ChannelId.E1).kind is ChannelKind.EXTERNAL_INPUT


def test_no_register_or_discrete_overlap_in_real_table() -> None:
    registers = [
        reg
        for spec in CHANNEL_SPECS.values()
        for reg in range(spec.value_register, spec.value_register + 7)
    ]
    discretes = [
        bit
        for spec in CHANNEL_SPECS.values()
        for bit in range(spec.status_discrete, spec.status_discrete + 8)
    ]
    assert len(registers) == len(set(registers))
    assert len(discretes) == len(set(discretes))


def test_overlapping_spec_fails_validation() -> None:
    overlapping = {
        ChannelId.I1: ChannelSpec(ChannelId.I1, ChannelKind.TRANSDUCER, 30001, 30003, 30006, 10001),
        # I2 deliberately collides with I1's register window.
        ChannelId.I2: ChannelSpec(ChannelId.I2, ChannelKind.TRANSDUCER, 30002, 30004, 30007, 10009),
    }
    with pytest.raises(ServomexConfigurationError, match="claimed by both"):
        channels._validate(overlapping)  # pyright: ignore[reportPrivateUsage]
