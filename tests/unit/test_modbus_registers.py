"""PDU address math over the full channel table."""

from __future__ import annotations

import pytest

from servomexlib.errors import ServomexConfigurationError
from servomexlib.protocol.modbus import registers as reg
from servomexlib.registry.channels import CHANNEL_SPECS, ChannelId, spec_for


@pytest.mark.parametrize("channel", list(ChannelId))
def test_value_name_unit_status_pdu_round_trip(channel: ChannelId) -> None:
    spec = spec_for(channel)
    assert reg.value_pdu(channel) == spec.value_register - reg.INPUT_REGISTER_BASE
    assert reg.name_pdu(channel) == spec.name_register - reg.INPUT_REGISTER_BASE
    assert reg.unit_pdu(channel) == spec.unit_register - reg.INPUT_REGISTER_BASE
    assert reg.status_pdu(channel) == spec.status_discrete - reg.DISCRETE_BASE


def test_known_pdu_anchors() -> None:
    assert reg.value_pdu(ChannelId.I1) == 0
    assert reg.name_pdu(ChannelId.I1) == 2
    assert reg.unit_pdu(ChannelId.I1) == 5
    assert reg.value_pdu(ChannelId.I2) == 7  # stride 7
    assert reg.status_pdu(ChannelId.E2) == 72  # 10073 - 10001


def test_analyser_and_coil_pdu() -> None:
    # Analyser status shares the FC02 space (− 10001): 11001 → PDU 1000.
    assert reg.analyser_status_pdu(reg.ANALYSER_FAULT_DISCRETE) == 1000
    assert reg.analyser_status_pdu(reg.ANALYSER_MAINTENANCE_DISCRETE) == 1001
    assert reg.analyser_status_pdu(reg.CAL_GROUP_DISCRETE_BASE) == 1008
    assert reg.cal_group_start_coil_pdu(1) == 0
    assert reg.cal_group_start_coil_pdu(4) == 3
    assert reg.stop_all_coil_pdu() == 8


def test_all_pdu_addresses_non_negative_and_unique() -> None:
    value_pdus = {reg.value_pdu(c) for c in CHANNEL_SPECS}
    assert all(p >= 0 for p in value_pdus)
    assert len(value_pdus) == len(CHANNEL_SPECS)


def test_bad_cal_group_raises() -> None:
    with pytest.raises(ServomexConfigurationError, match="cal-group must be 1-4"):
        reg.cal_group_start_coil_pdu(5)


def test_below_base_raises() -> None:
    with pytest.raises(ServomexConfigurationError, match="negative PDU"):
        reg.input_pdu(29000)
