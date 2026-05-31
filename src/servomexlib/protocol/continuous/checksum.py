"""Continuous-mode 16-bit additive checksum.

**Confirmed** against all 6 frames of the bench-4100D capture (memory
``servomex-continuous-checksum``): sum every byte **after** the leading
start-space, up to **and including** the ``;`` immediately preceding the
checksum field; take ``& 0xFFFF``; emit as 4 uppercase hex digits.

Excluded from the sum: the leading ``0x20`` start char, the checksum's own
4 chars, the closing ``;``, and the ``CR LF`` terminator.
"""

from __future__ import annotations

from servomexlib.errors import ErrorContext, ServomexChecksumError
from servomexlib.protocol.base import ProtocolKind

#: Number of hex characters in the trailing checksum field.
CHECKSUM_HEX_LEN = 4


def compute(body: bytes) -> int:
    """Compute the 16-bit additive checksum over ``body``.

    Args:
        body: The bytes to sum — everything after the leading start-space up to
            and including the ``;`` before the checksum field.

    Returns:
        The ``& 0xFFFF`` sum.
    """
    return sum(body) & 0xFFFF


# The frame content the parser hands us is ``<sp> ... <;> <CKSUM> <;>`` (the
# ``CR LF`` already stripped). The checksum field is the 4 chars before the final
# ``;``; the summed body runs from just past the start-space up to and including
# the ``;`` that precedes the checksum field.
_TRAILER_LEN = CHECKSUM_HEX_LEN + 1  # checksum field + closing ';'


def checksummed_body(frame: bytes) -> bytes:
    """Return the byte slice the checksum is computed over.

    ``frame`` is the content between the leading start-space and the ``CR LF``
    terminator: ``<sp>BODY;<CKSUM>;``. The summed region is ``frame[1:-_TRAILER_LEN]``
    — everything past the start-space through the ``;`` before the checksum field.
    """
    return frame[1:-_TRAILER_LEN]


def checksum_field(frame: bytes) -> bytes:
    """Return the 4-hex-digit checksum field read off ``frame``."""
    return frame[-_TRAILER_LEN:-1]


def verify(frame: bytes) -> int:
    """Recompute and verify the checksum carried in ``frame``.

    Args:
        frame: Frame content — ``<sp>BODY;<CKSUM>;`` with the ``CR LF`` stripped.

    Returns:
        The computed (and confirmed) checksum value.

    Raises:
        ServomexChecksumError: The frame is too short to hold a checksum, the
            checksum field is not valid hex, or the recomputed value does not
            match the field.
    """
    field = checksum_field(frame)
    try:
        expected = int(field, 16)
    except ValueError as exc:
        raise ServomexChecksumError(
            f"continuous-frame checksum field is not hex: {field!r}",
            context=_context(frame),
        ) from exc
    computed = compute(checksummed_body(frame))
    if computed != expected:
        raise ServomexChecksumError(
            f"continuous-frame checksum mismatch: computed {computed:04X}, "
            f"frame carried {field.decode('ascii', 'replace').upper()}",
            context=_context(frame, computed=computed, expected=expected),
        )
    return computed


def _context(
    frame: bytes, *, computed: int | None = None, expected: int | None = None
) -> ErrorContext:
    extra: dict[str, str] = {}
    if computed is not None:
        extra["computed"] = f"{computed:04X}"
    if expected is not None:
        extra["expected"] = f"{expected:04X}"
    return ErrorContext(
        protocol=ProtocolKind.CONTINUOUS_ASCII,
        response=frame,
        extra=extra,
    )


__all__ = [
    "CHECKSUM_HEX_LEN",
    "checksum_field",
    "checksummed_body",
    "compute",
    "verify",
]
