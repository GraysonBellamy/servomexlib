"""Property tests for the Modbus decode contract."""

from __future__ import annotations

import math

from anymodbus import ByteOrder, WordOrder
from anymodbus.decoders import decode_float32, encode_float32
from hypothesis import given
from hypothesis import strategies as st

from servomexlib.protocol.modbus.client import (
    _decode_name,  # pyright: ignore[reportPrivateUsage]
    _words_to_bytes,  # pyright: ignore[reportPrivateUsage]
)


@given(st.floats(allow_nan=False, allow_infinity=False, min_value=-1e6, max_value=1e6, width=32))
def test_float32_high_word_first_round_trip(value: float) -> None:
    # The HW-confirmed contract: high word first, big-endian bytes.
    words = encode_float32(value, word_order=WordOrder.HIGH_LOW, byte_order=ByteOrder.BIG)
    decoded = decode_float32(words, word_order=WordOrder.HIGH_LOW, byte_order=ByteOrder.BIG)
    assert decoded == value or math.isclose(decoded, value, rel_tol=1e-6, abs_tol=1e-9)


@given(st.lists(st.integers(min_value=0, max_value=0xFFFF), max_size=8))
def test_words_to_bytes_is_big_endian_and_total(words: list[int]) -> None:
    out = _words_to_bytes(words)
    assert len(out) == 2 * len(words)
    for i, word in enumerate(words):
        assert out[2 * i] == (word >> 8) & 0xFF
        assert out[2 * i + 1] == word & 0xFF


def test_decode_name_handles_pipes_spaces_and_nul() -> None:
    # ||||||  (unlabelled), spaces, and NUL padding all collapse to None/clean text.
    assert _decode_name(_words_to_bytes_for("||||||")) is None
    assert _decode_name([0, 0, 0]) is None
    assert _decode_name(_words_to_bytes_for("CO\x00\x00\x00\x00")) == "CO"


def _words_to_bytes_for(text: str) -> list[int]:
    data = text.encode("latin-1")
    data += b"\x00" * (-len(data) % 2)
    return [int.from_bytes(data[i : i + 2], "big") for i in range(0, len(data), 2)]
