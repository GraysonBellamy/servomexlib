"""Continuous-mode checksum: recompute the capture, reject corruption, round-trip."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from servomexlib.errors import ServomexChecksumError
from servomexlib.protocol.continuous import checksum


def test_recomputes_every_fixture_frame(continuous_frames: list[bytes]) -> None:
    assert len(continuous_frames) == 6
    for frame in continuous_frames:
        field = checksum.checksum_field(frame)
        value = checksum.verify(frame)
        assert f"{value:04X}".encode() == field


def test_flipped_byte_raises(continuous_frames: list[bytes]) -> None:
    frame = bytearray(continuous_frames[0])
    # Flip a byte inside the summed body (the 'O' of "Oxygen").
    idx = frame.index(b"Oxygen")
    frame[idx] ^= 0x01
    with pytest.raises(ServomexChecksumError):
        checksum.verify(bytes(frame))


def test_non_hex_checksum_field_raises() -> None:
    # 'ZZZZ' is not valid hex in the checksum slot.
    frame = b" 01;02;ZZZZ;"
    with pytest.raises(ServomexChecksumError):
        checksum.verify(frame)


@given(body=st.binary(min_size=0, max_size=200))
def test_build_compute_verify_round_trip(body: bytes) -> None:
    # Build a frame whose summed region is `body` + the pre-checksum ';'.
    summed = body + b";"
    value = checksum.compute(summed)
    frame = b" " + summed + f"{value:04X}".encode() + b";"
    assert checksum.verify(frame) == value
    assert checksum.checksummed_body(frame) == summed
