"""Continuous parser: golden regression + structural/flag coverage."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from servomexlib.errors import ServomexChecksumError, ServomexFrameError, ServomexParseError
from servomexlib.protocol.base import ProtocolKind
from servomexlib.protocol.continuous import checksum
from servomexlib.protocol.continuous.parser import parse_frame
from servomexlib.registry.channels import ChannelId, ChannelKind
from servomexlib.registry.units import Unit
from tests.conftest import approx

_FIXED_TS = datetime(2026, 5, 30, 12, 0, 0, tzinfo=UTC)


def _build_frame(header_fields: list[str], blocks: list[list[str]]) -> bytes:
    """Assemble a checksum-correct frame from raw string fields."""
    flat = list(header_fields)
    for block in blocks:
        flat.extend(block)
    summed = (";".join(flat) + ";").encode("ascii")
    value = checksum.compute(summed)
    return b" " + summed + f"{value:04X}".encode("ascii") + b";"


def test_golden_first_frame(continuous_frames: list[bytes]) -> None:
    frame = parse_frame(continuous_frames[0], received_at=_FIXED_TS, monotonic_ns=123)

    assert frame.protocol is ProtocolKind.CONTINUOUS_ASCII
    assert frame.received_at == _FIXED_TS
    assert frame.monotonic_ns == 123
    assert len(frame.readings) == 5

    o2, co, co2, e1, e2 = frame.readings
    assert o2.channel is ChannelId.I1
    assert o2.name == "Oxygen"
    assert o2.value == approx(20.376)
    assert o2.unit is Unit.PERCENT
    assert o2.kind is ChannelKind.TRANSDUCER
    assert co.name == "CO"
    assert co.value == approx(0.084)
    assert co2.name == "CO2"
    assert co2.value == approx(0.250)

    # External inputs: unlabelled (||||||) → None, 0.0 mA.
    assert e1.channel is ChannelId.E1
    assert e1.kind is ChannelKind.EXTERNAL_INPUT
    assert e1.name is None
    assert e1.value == approx(0.0)
    assert e1.unit is Unit.MILLIAMP
    assert e2.name is None

    # Idle: no faults, no alarms anywhere.
    assert all(r.status.ok for r in frame.readings)


def test_golden_analyser_status_idle(continuous_frames: list[bytes]) -> None:
    frame = parse_frame(continuous_frames[0])
    analyser = frame.analyser
    assert analyser.fault is False
    assert analyser.maintenance is False
    # S1S1S1S1 → four groups, sampling (not calibrating), cal-gas 1.
    assert len(analyser.cal_groups) == 4
    for i, group in enumerate(analyser.cal_groups, start=1):
        assert group.group == i
        assert group.calibrating is False
        assert group.cal_gas == 1
    assert analyser.clock == datetime(2020, 10, 6, 2, 54, 12)


def test_all_six_frames_parse(continuous_frames: list[bytes]) -> None:
    for frame_bytes in continuous_frames:
        frame = parse_frame(frame_bytes)
        assert len(frame.readings) == 5


def test_frame_channel_lookup_and_samples(continuous_frames: list[bytes]) -> None:
    frame = parse_frame(continuous_frames[0])
    assert frame.channel(ChannelId.I1).name == "Oxygen"
    samples = frame.as_samples(device="bench")
    assert len(samples) == 5
    assert samples[0].device == "bench"
    assert samples[0].channel is ChannelId.I1
    assert samples[0].requested_at is None  # passive continuous mode


def test_flipped_byte_raises_checksum(continuous_frames: list[bytes]) -> None:
    frame = bytearray(continuous_frames[0])
    frame[frame.index(b"Oxygen")] ^= 0x01
    with pytest.raises(ServomexChecksumError):
        parse_frame(bytes(frame))


def test_missing_start_space_raises_frame_error() -> None:
    with pytest.raises(ServomexFrameError):
        parse_frame(b"06-10-20;02:54:12;  ;S1S1S1S1;05;2A1D;")


def test_field_count_mismatch_raises_frame_error() -> None:
    # Declare N=02 but supply only one block → field count disagrees.
    frame = _build_frame(
        ["06-10-20", "02:54:12", "  ", "S1S1S1S1", "02"],
        [["I1", "Oxygen", "20.376", " % ", "    ", "  ", " ", " "]],
    )
    with pytest.raises(ServomexFrameError):
        parse_frame(frame)


def test_unknown_channel_id_raises_parse_error() -> None:
    frame = _build_frame(
        ["06-10-20", "02:54:12", "  ", "S1S1S1S1", "01"],
        [["Z9", "Oxygen", "20.376", " % ", "    ", "  ", " ", " "]],
    )
    with pytest.raises(ServomexParseError):
        parse_frame(frame)


def test_status_flags_decoded() -> None:
    # Fault+Maintenance on the channel, alarms 1 & 3 raised, calibrating, warming up.
    frame = _build_frame(
        ["06-10-20", "02:54:12", "FM", "C1S1S1S1", "01"],
        [["I1", "Oxygen", "20.376", " % ", "1 3 ", "FM", "C", "W"]],
    )
    parsed = parse_frame(frame)
    reading = parsed.readings[0]
    assert reading.status.fault is True
    assert reading.status.maintenance is True
    assert reading.status.calibrating is True
    assert reading.status.warming_up is True
    assert reading.status.alarms == (True, False, True, False)
    assert reading.status.ok is False

    # Analyser-level fault/maintenance + group 1 now calibrating.
    assert parsed.analyser.fault is True
    assert parsed.analyser.maintenance is True
    assert parsed.analyser.cal_groups[0].calibrating is True


def test_invalid_value_is_none() -> None:
    frame = _build_frame(
        ["06-10-20", "02:54:12", "  ", "S1S1S1S1", "01"],
        [["I1", "Oxygen", "++++++", " % ", "    ", "  ", " ", " "]],
    )
    assert parse_frame(frame).readings[0].value is None


def test_bad_clock_is_none() -> None:
    frame = _build_frame(
        ["zz-zz-zz", "zz:zz:zz", "  ", "S1S1S1S1", "01"],
        [["I1", "Oxygen", "20.376", " % ", "    ", "  ", " ", " "]],
    )
    assert parse_frame(frame).analyser.clock is None
