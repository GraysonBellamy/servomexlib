"""Channel identity + kind + the Modbus addressing spine.

This module is the heart of the device-fit core: a single declarative table
(:data:`CHANNEL_SPECS`) maps every channel to its addressing in **both** worlds.
The Modbus client reads register/discrete addresses from here; the continuous
parser reads ``kind`` from here; ``identify()`` walks it to report populated
slots. One source of truth, consulted by both protocols.

The table is **generated from the stride pattern** and **eagerly
validated at import** — a malformed or overlapping entry fails loud as
:class:`ServomexConfigurationError`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from servomexlib.errors import ErrorContext, ServomexConfigurationError


class ChannelId(StrEnum):
    """A channel slot on the analyser.

    ``I*`` are measured transducers, ``D*`` are derived channels, ``E*`` are
    external analogue (mA) inputs. The wire id matches the enum value (``"I1"``).
    """

    I1 = "I1"
    I2 = "I2"
    I3 = "I3"
    I4 = "I4"
    D1 = "D1"
    D2 = "D2"
    D3 = "D3"
    D4 = "D4"
    E1 = "E1"
    E2 = "E2"


class ChannelKind(StrEnum):
    """Classification of a channel slot.

    Drives the per-``kind`` status-bit exceptions: external
    inputs map bit 0 to ``Invalid`` rather than ``Fault``; derived channels
    carry copies of their parent transducer's flags.
    """

    TRANSDUCER = "transducer"
    DERIVED = "derived"
    EXTERNAL_INPUT = "external_input"


def kind_for(channel: ChannelId) -> ChannelKind:
    """Classify a :class:`ChannelId` by its id prefix.

    ``I*`` → transducer, ``D*`` → derived, ``E*`` → external input.
    """
    prefix = channel.value[0]
    if prefix == "I":
        return ChannelKind.TRANSDUCER
    if prefix == "D":
        return ChannelKind.DERIVED
    return ChannelKind.EXTERNAL_INPUT


# --- The addressing spine -------------------------------------------------

#: Float32 measurement value width, in input registers.
VALUE_REGISTERS = 2
#: Display-name string width, in input registers (6 chars / 3 regs).
NAME_REGISTERS = 3
#: Unit string width, in input registers (3 chars + NUL / 2 regs).
UNIT_REGISTERS = 2
#: Per-channel input-register stride: value(2) + name(3) + unit(2).
_REGISTER_STRIDE = VALUE_REGISTERS + NAME_REGISTERS + UNIT_REGISTERS
#: Per-channel discrete-input stride: 8 status bits.
_DISCRETE_STRIDE = 8


@dataclass(frozen=True, slots=True)
class ChannelSpec:
    """Addressing for one channel slot in both protocol worlds.

    The Modbus addresses are **data-model numbers** (1-based 3xxxx / 1xxxx);
    :mod:`servomexlib.protocol.modbus.registers` lowers them to 0-based PDU
    addresses. Continuous mode is self-describing, so the parser needs only the
    :attr:`kind` classification from this table — not the addresses.
    """

    channel: ChannelId
    kind: ChannelKind
    value_register: int  # data-model, float32 (VALUE_REGISTERS regs)
    name_register: int  # data-model, string (NAME_REGISTERS regs)
    unit_register: int  # data-model, string (UNIT_REGISTERS regs, trailing NUL)
    status_discrete: int  # data-model, stride-8 status bitmap base


# Block bases (data-model, 1-based) — confirmed against the bench 4100D
# (memory ``servomex-modbus-validation``).
_BLOCKS: tuple[tuple[str, int, int, int], ...] = (
    # (id_prefix, count, register_base, discrete_base)
    ("I", 4, 30001, 10001),  # transducers I1..I4 (value@30001 stride 7; discrete@10001 stride 8)
    ("D", 4, 30029, 10033),  # derived D1..D4
    ("E", 2, 30057, 10065),  # external mA inputs E1..E2
)


def _build_specs() -> dict[ChannelId, ChannelSpec]:
    specs: dict[ChannelId, ChannelSpec] = {}
    for prefix, count, register_base, discrete_base in _BLOCKS:
        for i in range(count):
            channel = ChannelId(f"{prefix}{i + 1}")
            value_register = register_base + _REGISTER_STRIDE * i
            specs[channel] = ChannelSpec(
                channel=channel,
                kind=kind_for(channel),
                value_register=value_register,
                name_register=value_register + VALUE_REGISTERS,
                unit_register=value_register + VALUE_REGISTERS + NAME_REGISTERS,
                status_discrete=discrete_base + _DISCRETE_STRIDE * i,
            )
    return specs


def _validate(specs: dict[ChannelId, ChannelSpec]) -> None:
    """Fail loud at import on any overlapping register or discrete range."""
    register_owner: dict[int, ChannelId] = {}
    discrete_owner: dict[int, ChannelId] = {}
    for spec in specs.values():
        for reg in range(spec.value_register, spec.value_register + _REGISTER_STRIDE):
            prior = register_owner.get(reg)
            if prior is not None:
                raise ServomexConfigurationError(
                    f"input register {reg} claimed by both {prior.value} and {spec.channel.value}",
                    context=ErrorContext(register=reg, channel=spec.channel),
                )
            register_owner[reg] = spec.channel
        for bit in range(spec.status_discrete, spec.status_discrete + _DISCRETE_STRIDE):
            prior = discrete_owner.get(bit)
            if prior is not None:
                raise ServomexConfigurationError(
                    f"discrete input {bit} claimed by both {prior.value} and {spec.channel.value}",
                    context=ErrorContext(register=bit, channel=spec.channel),
                )
            discrete_owner[bit] = spec.channel


#: The full channel addressing table, generated and validated at import.
CHANNEL_SPECS: dict[ChannelId, ChannelSpec] = _build_specs()
_validate(CHANNEL_SPECS)


def spec_for(channel: ChannelId) -> ChannelSpec:
    """Return the :class:`ChannelSpec` for ``channel``."""
    return CHANNEL_SPECS[channel]


__all__ = [
    "CHANNEL_SPECS",
    "NAME_REGISTERS",
    "UNIT_REGISTERS",
    "VALUE_REGISTERS",
    "ChannelId",
    "ChannelKind",
    "ChannelSpec",
    "kind_for",
    "spec_for",
]
