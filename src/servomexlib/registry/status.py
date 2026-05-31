"""Status-flag decoding from both wire formats.

One :class:`ChannelStatus` regardless of mode. The Modbus discrete bitmap
(FC02, stride-8) and the continuous-ASCII status fields both decode here so the
two per-``kind`` exceptions live in exactly one place:

- **EXTERNAL_INPUT (E1/E2):** discrete bit 0 is **``Invalid``** (not ``Fault``);
  bits 1-3 are reserved (no Maintenance / Calibration / WarmingUp on an analogue
  input). We surface ``Invalid`` through the ``fault`` field so
  :attr:`ChannelStatus.ok` is correct, and force bits 1-3 clear rather than
  reading them as cal/maintenance/warmup.
- **DERIVED (D1-D4):** bits 0-3 are **copies of the parent transducer's** flags,
  not independent signals. They decode the same way, but consumers treat a
  derived fault as derived (the analyser-level fault comes from ``11001``, so a
  D-channel "fault" is not double-counted).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from servomexlib.devices.models import AnalyserStatus, CalGroupState, ChannelStatus
from servomexlib.registry.channels import ChannelKind

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

#: Discrete-bitmap bit offsets for a standard (transducer/derived) channel.
_FAULT = 0
_MAINTENANCE = 1
_CALIBRATION = 2
_WARMING_UP = 3
_ALARM_BASE = 4
_ALARMS = 4
#: Number of status bits per channel.
DISCRETE_BITS = 8
#: Cal-group discretes pack two bits per group (calibrating + which gas).
_CAL_GROUPS = 4


def _bit(bits: Sequence[bool], index: int) -> bool:
    return index < len(bits) and bool(bits[index])


def _alarms(bits: Sequence[bool]) -> tuple[bool, bool, bool, bool]:
    a = tuple(_bit(bits, _ALARM_BASE + i) for i in range(_ALARMS))
    return (a[0], a[1], a[2], a[3])


def decode_discrete_status(bits: Sequence[bool], kind: ChannelKind) -> ChannelStatus:
    """Decode a Modbus FC02 stride-8 status bitmap into a :class:`ChannelStatus`.

    Applies the per-``kind`` bit-layout exceptions (see module
    docstring). ``bits`` shorter than :data:`DISCRETE_BITS` is tolerated —
    missing bits read as clear.
    """
    if kind is ChannelKind.EXTERNAL_INPUT:
        # bit 0 = Invalid (surfaced as not-ok); bits 1-3 reserved → forced clear.
        return ChannelStatus(
            fault=_bit(bits, _FAULT),
            maintenance=False,
            calibrating=False,
            warming_up=False,
            alarms=_alarms(bits),
        )
    return ChannelStatus(
        fault=_bit(bits, _FAULT),
        maintenance=_bit(bits, _MAINTENANCE),
        calibrating=_bit(bits, _CALIBRATION),
        warming_up=_bit(bits, _WARMING_UP),
        alarms=_alarms(bits),
    )


def decode_continuous_status(
    *,
    fm: bytes,
    alarms: bytes,
    cal: bytes,
    warmup: bytes,
) -> ChannelStatus:
    """Decode the continuous-ASCII per-channel status fields.

    Continuous mode is self-describing — the analyser sends explicit ``F``/``M``/
    ``C``/``W`` characters and a 4-char alarm field — so there is no bit-layout
    ambiguity to reconcile per ``kind`` here; the fields mean what they say.
    Centralised alongside the Modbus decode so both modes share one decoder.
    """
    return ChannelStatus(
        fault=fm[0:1] == b"F",
        maintenance=fm[1:2] == b"M",
        calibrating=cal.strip() == b"C",
        warming_up=warmup.strip() == b"W",
        alarms=(
            alarms[0:1] not in (b" ", b""),
            alarms[1:2] not in (b" ", b""),
            alarms[2:3] not in (b" ", b""),
            alarms[3:4] not in (b" ", b""),
        ),
    )


def decode_analyser_status(
    *,
    fault: bool,
    maintenance: bool,
    cal_group_bits: Sequence[bool],
    clock: datetime | None = None,
) -> AnalyserStatus:
    """Decode the Modbus analyser-status discretes into an :class:`AnalyserStatus`.

    ``cal_group_bits`` is the 8-bit block ``11009``-``11016`` — two bits per
    cal-group: the first selects sample vs calibrate, the second selects which
    calibration gas (gas-2 when set, else gas-1).
    """
    groups = tuple(
        CalGroupState(
            group=g + 1,
            calibrating=_bit(cal_group_bits, 2 * g),
            cal_gas=2 if _bit(cal_group_bits, 2 * g + 1) else 1,
        )
        for g in range(_CAL_GROUPS)
    )
    return AnalyserStatus(
        fault=fault,
        maintenance=maintenance,
        cal_groups=groups,
        clock=clock,
    )


__all__ = [
    "DISCRETE_BITS",
    "decode_analyser_status",
    "decode_continuous_status",
    "decode_discrete_status",
]
