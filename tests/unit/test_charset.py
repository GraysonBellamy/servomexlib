"""Display-charset decoding: glyph map, NUL stripping, never-raises."""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from servomexlib.registry.charset import decode_display


def test_subscript_two_glyph() -> None:
    # CO2 name from the bench unit: 43 4F 82 20 20 20 → "CO₂  " → stripped "CO₂".
    assert decode_display(b"\x43\x4f\x82\x20\x20\x20").rstrip() == "CO₂"


def test_plain_ascii_passthrough() -> None:
    assert decode_display(b"Oxygen") == "Oxygen"


def test_strip_null_on_unit_field() -> None:
    assert decode_display(b" %\x00", strip_null=True) == " %"
    assert decode_display(b" %\x00", strip_null=False) == " %\x00"


@given(data=st.binary())
def test_never_raises_and_preserves_length(data: bytes) -> None:
    result = decode_display(data)
    assert isinstance(result, str)
    # Every byte maps to exactly one character.
    assert len(result) == len(data)
