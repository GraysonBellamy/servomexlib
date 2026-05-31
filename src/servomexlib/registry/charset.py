"""Servomex display-ROM glyph → Unicode decoding.

Modbus name/unit registers use the analyser's display character ROM, **not**
ASCII: CO₂'s name comes back as bytes ``43 4F 82 20 20 20`` where ``0x82`` is the
subscript-2 glyph (continuous mode substitutes plain ASCII ``"CO2"``). Both the
Modbus codec and the continuous parser route names/units through here so a
decoded name is always clean Unicode regardless of mode.

:func:`decode_display` is **pure and never raises** — unknown bytes fall back
through ``latin-1`` (every byte 0x00–0xFF is valid latin-1), so arbitrary input
always produces a string.
"""

from __future__ import annotations

# Servomex display-ROM code points that are not plain ASCII. Confirmed against
# the bench 4100D (memory ``servomex-modbus-validation``); extend as further
# glyphs are characterised.
_GLYPHS: dict[int, str] = {
    0x82: "₂",  # subscript two — e.g. CO₂, NO₂
}


def decode_display(data: bytes, *, strip_null: bool = False) -> str:
    r"""Decode display-ROM ``data`` to Unicode. Never raises.

    Each byte is mapped through the Servomex glyph table when known, otherwise
    decoded as ``latin-1`` (a total mapping over all byte values). With
    ``strip_null=True`` the trailing ``NUL`` padding on Modbus unit fields is
    removed.

    Args:
        data: Raw bytes from a name or unit register/field.
        strip_null: Strip trailing ``\\x00`` padding (unit fields).

    Returns:
        The decoded string.
    """
    chars = [_GLYPHS.get(byte) or chr(byte) for byte in data]
    text = "".join(chars)
    if strip_null:
        text = text.rstrip("\x00")
    return text


__all__ = ["decode_display"]
