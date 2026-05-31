"""Pure data-model → PDU address math.

The registry (:mod:`servomexlib.registry.channels`) holds **data-model** numbers
(1-based 3xxxx / 1xxxx / 0xxxx). On the wire, Modbus function codes address a
0-based PDU space *per code*: input registers (FC04) start at PDU 0 = data-model
30001, discrete inputs (FC02) at PDU 0 = 10001, and coils (FC01/05) at PDU 0 = 1.
These functions do that arithmetic and nothing else — they are pure and
property-tested over the whole table.

The analyser-status discretes (``11001``/``11002`` fault/maintenance, ``11009``-
``11016`` cal-group flags) are a *separate logical block* but share the single
FC02 discrete-input space, so they lower with the same ``− 10001`` rule (manual
Appendix B, §3.5: "the Modbus address … is calculated by adding the offset to the
discrete input value"). Hence analyser-fault ``11001`` is PDU 1000, not PDU 0 —
reading it as ``− 11001`` would collide with I1's status at PDU 0.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from servomexlib.errors import ErrorContext, ServomexConfigurationError
from servomexlib.registry.channels import (
    NAME_REGISTERS,
    UNIT_REGISTERS,
    VALUE_REGISTERS,
    spec_for,
)

if TYPE_CHECKING:
    from servomexlib.registry.channels import ChannelId

#: Data-model base of the input-register space (FC04).
INPUT_REGISTER_BASE = 30001
#: Data-model base of the per-channel discrete-input space (FC02).
DISCRETE_BASE = 10001
#: Data-model base of the analyser-status discrete block (FC02).
ANALYSER_STATUS_BASE = 11001
#: Data-model base of the coil space (FC01/FC05).
COIL_BASE = 1

#: Analyser-status discretes (data-model).
ANALYSER_FAULT_DISCRETE = 11001
ANALYSER_MAINTENANCE_DISCRETE = 11002
#: First of the 8 cal-group discretes (``11009``-``11016``).
CAL_GROUP_DISCRETE_BASE = 11009
CAL_GROUP_DISCRETE_COUNT = 8

#: Coils that start cal-group 1-4 (data-model); a 0→1 pulse triggers the action.
CAL_GROUP_START_COILS: dict[int, int] = {1: 1, 2: 2, 3: 3, 4: 4}
#: Coil that stops all cal-groups.
STOP_ALL_COIL = 9


def _to_pdu(data_model: int, base: int, *, space: str) -> int:
    pdu = data_model - base
    if pdu < 0:
        raise ServomexConfigurationError(
            f"{space} data-model {data_model} is below base {base} (negative PDU)",
            context=ErrorContext(register=data_model),
        )
    return pdu


def input_pdu(data_model: int) -> int:
    """Lower an input-register (3xxxx) data-model number to its PDU address."""
    return _to_pdu(data_model, INPUT_REGISTER_BASE, space="input register")


def discrete_pdu(data_model: int) -> int:
    """Lower a per-channel discrete-input (1xxxx) number to its PDU address."""
    return _to_pdu(data_model, DISCRETE_BASE, space="discrete input")


def analyser_status_pdu(data_model: int) -> int:
    """Lower an analyser-status discrete (11xxx) number to its PDU address.

    Shares the single FC02 discrete-input space, so it uses the same
    :data:`DISCRETE_BASE` (``− 10001``) rule as the per-channel discretes — the
    ``11xxx`` numbers are simply a separate block higher in that space (manual
    Appendix B). Analyser-fault ``11001`` → PDU 1000.
    """
    return _to_pdu(data_model, DISCRETE_BASE, space="analyser-status discrete")


def coil_pdu(data_model: int) -> int:
    """Lower a coil (0xxxx) number to its PDU address."""
    return _to_pdu(data_model, COIL_BASE, space="coil")


# --- Channel-aware helpers (consume the registry) ------------------------


def value_pdu(channel: ChannelId) -> int:
    """PDU address of ``channel``'s float32 value (FC04, VALUE_REGISTERS regs)."""
    return input_pdu(spec_for(channel).value_register)


def name_pdu(channel: ChannelId) -> int:
    """PDU address of ``channel``'s display-name string (FC04, NAME_REGISTERS regs)."""
    return input_pdu(spec_for(channel).name_register)


def unit_pdu(channel: ChannelId) -> int:
    """PDU address of ``channel``'s unit string (FC04, UNIT_REGISTERS regs)."""
    return input_pdu(spec_for(channel).unit_register)


def status_pdu(channel: ChannelId) -> int:
    """PDU base of ``channel``'s 8-bit status bitmap (FC02)."""
    return discrete_pdu(spec_for(channel).status_discrete)


def cal_group_start_coil_pdu(group: int) -> int:
    """PDU address of the coil that starts cal-``group`` (1-4)."""
    if group not in CAL_GROUP_START_COILS:
        raise ServomexConfigurationError(
            f"cal-group must be 1-4 (got {group})",
            context=ErrorContext(extra={"group": group}),
        )
    return coil_pdu(CAL_GROUP_START_COILS[group])


def stop_all_coil_pdu() -> int:
    """PDU address of the stop-all-calibration coil."""
    return coil_pdu(STOP_ALL_COIL)


__all__ = [
    "ANALYSER_FAULT_DISCRETE",
    "ANALYSER_MAINTENANCE_DISCRETE",
    "ANALYSER_STATUS_BASE",
    "CAL_GROUP_DISCRETE_BASE",
    "CAL_GROUP_DISCRETE_COUNT",
    "CAL_GROUP_START_COILS",
    "COIL_BASE",
    "DISCRETE_BASE",
    "INPUT_REGISTER_BASE",
    "NAME_REGISTERS",
    "STOP_ALL_COIL",
    "UNIT_REGISTERS",
    "VALUE_REGISTERS",
    "analyser_status_pdu",
    "cal_group_start_coil_pdu",
    "coil_pdu",
    "discrete_pdu",
    "input_pdu",
    "name_pdu",
    "status_pdu",
    "stop_all_coil_pdu",
    "unit_pdu",
    "value_pdu",
]
