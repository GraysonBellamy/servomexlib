"""Pure continuous-frame parser.

:func:`parse_frame` turns one raw continuous-ASCII frame into a :class:`Frame`.
It is a pure function (given the acquisition timestamps): strip the start char,
split on ``;``, verify the checksum, validate the field count against the
declared ``N``, decode the 5-field header and the ``N`` eight-field blocks, and
route names/units through the display charset.

Frame grammar (verified byte-for-byte against the bench fixture)::

    <sp> date;time;FM(2);autocal(8);N(2) ( id;name(6);value(6);unit(3);
        alarms(4);FM(2);cal(1);warmup(1) ){N} CKSUM ; <CR><LF>
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

from servomexlib.devices.models import (
    AnalyserStatus,
    CalGroupState,
    Frame,
    Reading,
)
from servomexlib.errors import ErrorContext, ServomexFrameError, ServomexParseError
from servomexlib.protocol.base import ProtocolKind
from servomexlib.protocol.continuous import checksum
from servomexlib.registry.channels import ChannelId, kind_for
from servomexlib.registry.charset import decode_display
from servomexlib.registry.status import decode_continuous_status
from servomexlib.registry.units import coerce_unit

_HEADER_FIELDS = 5
_BLOCK_FIELDS = 8
_TRAILER_FIELDS = 2  # checksum field + the empty token after the closing ';'
_CAL_GROUPS = 4
_START = 0x20  # leading start-space


def parse_frame(
    raw: bytes,
    *,
    received_at: datetime | None = None,
    monotonic_ns: int | None = None,
) -> Frame:
    """Parse one continuous-ASCII frame into a :class:`Frame`.

    Args:
        raw: One frame, with or without the trailing ``CR LF``.
        received_at: Wall-clock UTC acquisition time. Defaults to "now".
        monotonic_ns: Monotonic acquisition time (join key). Defaults to "now".

    Returns:
        The decoded :class:`Frame`.

    Raises:
        ServomexFrameError: Missing start-space, missing closing ``;``, or the
            field count does not match the declared ``N``.
        ServomexParseError: A header or block field could not be decoded.
        ServomexChecksumError: The recomputed checksum did not match.
    """
    if received_at is None:
        received_at = datetime.now(UTC)
    if monotonic_ns is None:
        monotonic_ns = time.monotonic_ns()

    content = raw.rstrip(b"\r\n")
    if not content or content[0] != _START:
        raise ServomexFrameError(
            "continuous frame does not start with the 0x20 start-space",
            context=_ctx(raw),
        )

    # Corruption shows up as a checksum mismatch before we trust any field.
    checksum.verify(content)

    fields = content[1:].split(b";")
    if len(fields) < _HEADER_FIELDS + _TRAILER_FIELDS or fields[-1] != b"":
        raise ServomexFrameError(
            f"continuous frame has too few fields or no closing ';' (got {len(fields)} tokens)",
            context=_ctx(raw),
        )

    n_channels = _parse_int(fields[4], raw, label="N")
    expected = _HEADER_FIELDS + _BLOCK_FIELDS * n_channels + _TRAILER_FIELDS
    if len(fields) != expected:
        raise ServomexFrameError(
            f"continuous frame field count {len(fields)} does not match N={n_channels} "
            f"(expected {expected})",
            context=_ctx(raw),
        )

    analyser = _parse_header(fields[:_HEADER_FIELDS], raw)
    readings = tuple(
        _parse_block(
            fields[_HEADER_FIELDS + _BLOCK_FIELDS * i : _HEADER_FIELDS + _BLOCK_FIELDS * (i + 1)],
            raw=raw,
            received_at=received_at,
            monotonic_ns=monotonic_ns,
        )
        for i in range(n_channels)
    )
    return Frame(
        readings=readings,
        analyser=analyser,
        protocol=ProtocolKind.CONTINUOUS_ASCII,
        received_at=received_at,
        monotonic_ns=monotonic_ns,
        raw=content,
    )


def _parse_header(header: list[bytes], raw: bytes) -> AnalyserStatus:
    date_b, time_b, fm_b, autocal_b, _n_b = header
    fault = fm_b[0:1] == b"F"
    maintenance = fm_b[1:2] == b"M"
    cal_groups = tuple(_parse_cal_group(autocal_b, g) for g in range(_CAL_GROUPS))
    return AnalyserStatus(
        fault=fault,
        maintenance=maintenance,
        cal_groups=cal_groups,
        clock=_parse_clock(date_b, time_b),
    )


def _parse_cal_group(autocal: bytes, group: int) -> CalGroupState:
    seg = autocal[group * 2 : group * 2 + 2]
    calibrating = seg[0:1] == b"C"
    gas_byte = seg[1:2]
    cal_gas = int(gas_byte) if gas_byte in (b"1", b"2") else 1
    return CalGroupState(group=group + 1, calibrating=calibrating, cal_gas=cal_gas)


def _parse_clock(date_b: bytes, time_b: bytes) -> datetime | None:
    # The analyser clock may be unset/wrong — never raise; return None on any failure.
    try:
        day, month, year = (int(p) for p in date_b.split(b"-"))
        hour, minute, second = (int(p) for p in time_b.split(b":"))
        return datetime(2000 + year, month, day, hour, minute, second)
    except (ValueError, TypeError):
        return None


def _parse_block(
    block: list[bytes],
    *,
    raw: bytes,
    received_at: datetime,
    monotonic_ns: int,
) -> Reading:
    id_b, name_b, value_b, unit_b, alarms_b, fm_b, cal_b, warmup_b = block
    channel = _parse_channel_id(id_b, raw)
    status = decode_continuous_status(fm=fm_b, alarms=alarms_b, cal=cal_b, warmup=warmup_b)
    return Reading(
        channel=channel,
        kind=kind_for(channel),
        name=_parse_name(name_b),
        value=_parse_value(value_b),
        unit=coerce_unit(decode_display(unit_b, strip_null=True)),
        status=status,
        protocol=ProtocolKind.CONTINUOUS_ASCII,
        received_at=received_at,
        monotonic_ns=monotonic_ns,
        raw=b";".join(block),
    )


def _parse_channel_id(id_b: bytes, raw: bytes) -> ChannelId:
    text = id_b.decode("ascii", "replace").strip()
    try:
        return ChannelId(text)
    except ValueError as exc:
        raise ServomexParseError(
            f"unknown channel id {text!r} in continuous frame",
            context=_ctx(raw),
        ) from exc


def _parse_name(name_b: bytes) -> str | None:
    text = decode_display(name_b).strip()
    if not text or set(text) <= {"|"}:
        return None
    return text


def _parse_value(value_b: bytes) -> float | None:
    text = value_b.decode("ascii", "replace").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None  # over-range / non-numeric sentinel


def _parse_int(field: bytes, raw: bytes, *, label: str) -> int:
    try:
        return int(field.decode("ascii", "replace").strip())
    except ValueError as exc:
        raise ServomexParseError(
            f"continuous frame {label} field is not an integer: {field!r}",
            context=_ctx(raw),
        ) from exc


def _ctx(raw: bytes) -> ErrorContext:
    return ErrorContext(protocol=ProtocolKind.CONTINUOUS_ASCII, response=raw)


__all__ = ["parse_frame"]
