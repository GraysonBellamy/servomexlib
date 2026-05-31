"""Status decode from both wire formats incl. the per-kind exceptions."""

from __future__ import annotations

from servomexlib.registry.channels import ChannelKind
from servomexlib.registry.status import (
    decode_analyser_status,
    decode_continuous_status,
    decode_discrete_status,
)


def _bits(*on: int, width: int = 8) -> list[bool]:
    return [i in on for i in range(width)]


def test_transducer_standard_bitmap() -> None:
    # bits 0..7 = Fault, Maintenance, Calibration, WarmingUp, Alarm1..4
    status = decode_discrete_status(_bits(0, 3, 5), ChannelKind.TRANSDUCER)
    assert status.fault
    assert status.warming_up
    assert not status.maintenance
    assert not status.calibrating
    assert status.alarms == (False, True, False, False)
    assert not status.ok


def test_external_input_bit0_is_invalid_not_naive_cal() -> None:
    # E-channel: bit 0 = Invalid; bits 1-3 reserved → must read clear, not as
    # maintenance/calibration/warmup.
    status = decode_discrete_status(_bits(0, 1, 2, 3), ChannelKind.EXTERNAL_INPUT)
    assert status.fault  # Invalid surfaced through fault so .ok is correct
    assert not status.maintenance
    assert not status.calibrating
    assert not status.warming_up
    assert not status.ok


def test_external_input_valid_is_ok() -> None:
    status = decode_discrete_status(_bits(), ChannelKind.EXTERNAL_INPUT)
    assert status.ok


def test_derived_decodes_like_transducer() -> None:
    status = decode_discrete_status(_bits(0), ChannelKind.DERIVED)
    assert status.fault


def test_short_bitmap_tolerated() -> None:
    status = decode_discrete_status([True], ChannelKind.TRANSDUCER)
    assert status.fault
    assert status.alarms == (False, False, False, False)


def test_continuous_status_fields() -> None:
    status = decode_continuous_status(fm=b"FM", alarms=b"1   ", cal=b"C", warmup=b"W")
    assert status.fault
    assert status.maintenance
    assert status.calibrating
    assert status.warming_up
    assert status.alarms == (True, False, False, False)


def test_continuous_status_idle() -> None:
    status = decode_continuous_status(fm=b"  ", alarms=b"    ", cal=b" ", warmup=b" ")
    assert status.ok


def test_analyser_status_cal_groups() -> None:
    # group 1 calibrating on gas 2 → bits 0,1 set; others idle.
    status = decode_analyser_status(fault=False, maintenance=True, cal_group_bits=_bits(0, 1))
    assert not status.fault
    assert status.maintenance
    assert len(status.cal_groups) == 4
    assert status.cal_groups[0].calibrating
    assert status.cal_groups[0].cal_gas == 2
    assert not status.cal_groups[1].calibrating
    assert status.cal_groups[1].cal_gas == 1
