"""Model helpers: ChannelStatus.ok, Reading.as_dict/__format__, Frame.channel."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from servomexlib.devices.models import ChannelStatus, Reading
from servomexlib.errors import ServomexValidationError
from servomexlib.protocol.base import ProtocolKind
from servomexlib.protocol.continuous.parser import parse_frame
from servomexlib.registry.channels import ChannelId, ChannelKind
from servomexlib.registry.units import Unit
from tests.conftest import approx

_OK = ChannelStatus(False, False, False, False, (False, False, False, False))


def test_channel_status_ok() -> None:
    assert _OK.ok is True
    assert ChannelStatus(True, False, False, False, (False, False, False, False)).ok is False
    assert ChannelStatus(False, False, False, False, (False, True, False, False)).ok is False


def _reading(value: float | None) -> Reading:
    return Reading(
        channel=ChannelId.I1,
        kind=ChannelKind.TRANSDUCER,
        name="Oxygen",
        value=value,
        unit=Unit.PERCENT,
        status=_OK,
        protocol=ProtocolKind.CONTINUOUS_ASCII,
        received_at=datetime.now(UTC),
        monotonic_ns=0,
        raw=b"I1;Oxygen;20.376; % ;    ;  ; ; ",
    )


def test_reading_as_dict_flattens_booleans_to_ints() -> None:
    row = _reading(20.376).as_dict()
    assert row["channel"] == "I1"
    assert row["value"] == approx(20.376)
    assert row["unit"] == "%"
    assert row["ok"] == 1
    assert row["alarm1"] == 0


def test_reading_format_delegates_to_value() -> None:
    assert f"{_reading(20.376):.2f}" == "20.38"
    assert f"{_reading(None):.2f}" == "None"


def test_frame_channel_missing_raises(continuous_frames: list[bytes]) -> None:
    frame = parse_frame(continuous_frames[0])
    # I4 is not present in this 5-channel unit.
    with pytest.raises(ServomexValidationError):
        frame.channel(ChannelId.I4)
