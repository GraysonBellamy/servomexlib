"""Public testing seam.

Re-exports the in-process :class:`FakeTransport` plus fixture helpers so tests —
in this package and in downstream code — can drive every protocol without
hardware. Fixtures use the human-readable ``> hex`` / ``< hex`` arrow format
shared with the sibling ``.testing`` modules.

For the Modbus path we do **not** hand-roll an ADU simulator: the
:func:`mock_modbus_pair` helper preloads the byte-accurate
``anymodbus.testing.MockSlave`` (RTU **and** ASCII framing, FC01/02/04/05/08) with
the 4100's register/coil banks and binds *our* ``ModbusClient`` to it over an
in-process serial pair. The Modbus imports are lazy so ``servomexlib.testing``
stays importable without the optional ``[modbus]`` extra.
"""

from __future__ import annotations

import struct
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from servomexlib.transport.fake import FakeTransport, ScriptedReply

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Sequence

    from anymodbus import Framing
    from anymodbus.testing import MockSlave

    from servomexlib.protocol.modbus.client import ModbusClient
    from servomexlib.registry.channels import ChannelId
    from servomexlib.transport.base import Transport

__all__ = [
    "DEFAULT_4100_BANK",
    "CoilOp",
    "FakeTransport",
    "MockChannel",
    "ReadOp",
    "ScriptedReply",
    "coil_ops",
    "load_4100_banks",
    "load_arrow_script",
    "load_cal_state",
    "mock_modbus_pair",
    "mock_modbus_transport",
    "read_ops",
    "split_continuous_frames",
]


def split_continuous_frames(capture: bytes) -> list[bytes]:
    """Split a raw continuous capture into individual frame payloads (no CRLF).

    The on-wire terminator is ``CR LF`` (design §3.1), but a capture that has been
    through a text-mode transform keeps only the ``LF``. We split on ``LF`` and
    drop a trailing ``CR`` so both framings yield the same payloads — matching the
    parser, which strips ``CR LF`` either way.
    """
    return [frame.rstrip(b"\r") for frame in capture.split(b"\n") if frame.strip(b" \r")]


def load_arrow_script(text: str) -> dict[bytes, bytes]:
    """Parse a ``> hex`` / ``< hex`` arrow fixture into a write→reply script.

    Each ``>`` line is a request (host→device) and the following ``<`` line is
    the reply (device→host). ``#`` starts a comment; blank lines are ignored.
    Hex tokens may be space- or colon-separated.
    """
    script: dict[bytes, bytes] = {}
    pending: bytes | None = None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        marker, _, payload = line.partition(" ")
        data = _hex(payload)
        if marker == ">":
            pending = data
        elif marker == "<" and pending is not None:
            script[pending] = data
            pending = None
    return script


def _hex(payload: str) -> bytes:
    cleaned = payload.replace(" ", "").replace(":", "").replace(",", "")
    return bytes.fromhex(cleaned)


# --- Byte-accurate Modbus fake (MockSlave-backed) ------------------------


@dataclass(frozen=True, slots=True)
class CoilOp:
    """One observed coil operation against a recording :class:`MockSlave`.

    Used by the autocalibration coil-pulse tests to assert
    the **ordered** 0→1→0 transition: a ``start_calibration`` pulse should record
    a ``WRITE_SINGLE_COIL on=True`` (FC05), then a ``READ_COILS`` readback
    (FC01), then a ``WRITE_SINGLE_COIL on=False``. ``coil`` is the PDU address.
    """

    function_code: int
    coil: int
    on: bool | None  # FC05: the written state; FC01 readback: None


@dataclass(frozen=True, slots=True)
class ReadOp:
    """One observed read request against a recording :class:`MockSlave`."""

    function_code: int
    address: int
    count: int


_FC_WRITE_SINGLE_COIL = 0x05
_FC_READ_COILS = 0x01
_FC_READ_DISCRETE_INPUTS = 0x02
_FC_READ_HOLDING_REGISTERS = 0x03
_FC_READ_INPUT_REGISTERS = 0x04
_COIL_ON_VALUE = 0xFF00
#: A coil request PDU is ``fc + addr(2) + value/count(2)`` = 5 bytes.
_COIL_PDU_LEN = 5


def _decode_coil_op(pdu: bytes) -> CoilOp | None:
    """Decode a request PDU into a :class:`CoilOp` for FC05/FC01, else ``None``."""
    fc = pdu[0]
    if fc == _FC_WRITE_SINGLE_COIL and len(pdu) >= _COIL_PDU_LEN:
        _, addr, value = struct.unpack(">BHH", pdu[:_COIL_PDU_LEN])
        return CoilOp(function_code=fc, coil=addr, on=value == _COIL_ON_VALUE)
    if fc == _FC_READ_COILS and len(pdu) >= _COIL_PDU_LEN:  # the 0→1 readback
        _, addr, _count = struct.unpack(">BHH", pdu[:_COIL_PDU_LEN])
        return CoilOp(function_code=fc, coil=addr, on=None)
    return None


def _decode_read_op(pdu: bytes) -> ReadOp | None:
    """Decode a request PDU into a :class:`ReadOp` for read FCs, else ``None``."""
    fc = pdu[0]
    if fc not in {
        _FC_READ_COILS,
        _FC_READ_DISCRETE_INPUTS,
        _FC_READ_HOLDING_REGISTERS,
        _FC_READ_INPUT_REGISTERS,
    }:
        return None
    _, addr, count = struct.unpack(">BHH", pdu[:_COIL_PDU_LEN])
    return ReadOp(function_code=fc, address=addr, count=count)


_recording_slave_cache: type[MockSlave] | None = None


def _recording_slave_cls() -> type[MockSlave]:
    """Lazily build (and cache) a ``MockSlave`` subclass that logs coil ops.

    Defined lazily because ``anymodbus`` is an optional extra — importing
    :class:`MockSlave` at module load would break the ``servomexlib.testing``
    import for continuous-only installs.
    """
    global _recording_slave_cache  # noqa: PLW0603 — one-time lazy class cache
    if _recording_slave_cache is None:
        from anymodbus.testing import MockSlave as _MockSlave

        class RecordingMockSlave(_MockSlave):
            """A ``MockSlave`` that appends each coil op to ``coil_ops`` in order."""

            def __init__(self, *args: object, **kwargs: object) -> None:
                super().__init__(*args, **kwargs)  # type: ignore[arg-type]
                self.coil_ops: list[CoilOp] = []
                self.read_ops: list[ReadOp] = []

            def _handle_request(self, pdu: bytes) -> bytes:
                read_op = _decode_read_op(pdu)
                if read_op is not None:
                    self.read_ops.append(read_op)
                op = _decode_coil_op(pdu)
                if op is not None:
                    self.coil_ops.append(op)
                return super()._handle_request(pdu)

        _recording_slave_cache = RecordingMockSlave
    return _recording_slave_cache


def coil_ops(slave: MockSlave) -> list[CoilOp]:
    """Return the ordered coil ops a recording slave observed (``[]`` otherwise)."""
    return list(getattr(slave, "coil_ops", []))


def read_ops(slave: MockSlave) -> list[ReadOp]:
    """Return the ordered read ops a recording slave observed (``[]`` otherwise)."""
    return list(getattr(slave, "read_ops", []))


@dataclass(frozen=True, slots=True)
class MockChannel:
    """One channel's contents to preload into a :class:`MockSlave`'s banks.

    ``name`` / ``unit`` are **raw display-ROM bytes** (not ASCII), so a test can
    reproduce e.g. CO₂'s name ``43 4F 82 20 20 20`` and exercise the charset
    decode. ``status_bits`` are the 8 FC02 discrete bits (Fault, Maintenance,
    Calibration, WarmingUp, Alarm1..4).
    """

    channel: ChannelId
    value: float
    name: bytes = b"\x00\x00\x00\x00\x00\x00"
    unit: bytes = b"\x00\x00\x00"
    status_bits: tuple[bool, ...] = field(default=(False,) * 8)


def _default_bank() -> tuple[MockChannel, ...]:
    # The N=05 bench capture: 3 transducers + 2 external mA inputs.
    from servomexlib.registry.channels import ChannelId

    return (
        MockChannel(ChannelId.I1, 20.378, name=b"Oxygen", unit=b"%\x00\x00"),
        MockChannel(ChannelId.I2, 0.084, name=b"CO\x20\x20\x20\x20", unit=b"%\x00\x00"),
        # CO₂ via the display ROM: 'C','O',0x82(subscript-2),space,space,space.
        MockChannel(ChannelId.I3, 0.250, name=b"\x43\x4f\x82\x20\x20\x20", unit=b"%\x00\x00"),
        MockChannel(ChannelId.E1, 0.0, unit=b"mA\x00"),
        MockChannel(ChannelId.E2, 0.0, unit=b"mA\x00"),
    )


#: The default preloaded bank — matches the continuous capture's values exactly.
DEFAULT_4100_BANK: tuple[MockChannel, ...] = _default_bank()


def _bytes_to_words(data: bytes) -> list[int]:
    padded = data + b"\x00" * (-len(data) % 2)
    return [int.from_bytes(padded[i : i + 2], "big") for i in range(0, len(padded), 2)]


def _set_bit(bank: bytearray, index: int, *, on: bool) -> None:
    # Mirrors MockSlave's packing: bit `index` lives at byte index>>3, pos index&7.
    if on:
        bank[index >> 3] |= 1 << (index & 7)
    else:
        bank[index >> 3] &= (~(1 << (index & 7))) & 0xFF


def _inside_any_range(address: int, count: int, ranges: Sequence[tuple[int, int]]) -> bool:
    end = address + count
    return any(address >= start and end <= stop for start, stop in ranges)


def load_4100_banks(slave: MockSlave, channels: Sequence[MockChannel] = DEFAULT_4100_BANK) -> None:
    """Preload ``slave``'s input-register and discrete-input banks from ``channels``.

    Values are written as IEEE-754 float32 high-word-first (the HW-confirmed word
    order); names/units as raw display-ROM register words; status as FC02 bits.
    """
    from anymodbus.decoders import encode_float32

    from servomexlib.protocol.modbus import registers as reg

    for ch in channels:
        hi, lo = encode_float32(ch.value)  # defaults: HIGH_LOW + BIG (HW-confirmed)
        vpdu = reg.value_pdu(ch.channel)
        slave.input_registers[vpdu] = hi
        slave.input_registers[vpdu + 1] = lo
        for offset, word in enumerate(_bytes_to_words(ch.name)):
            slave.input_registers[reg.name_pdu(ch.channel) + offset] = word
        for offset, word in enumerate(_bytes_to_words(ch.unit)):
            slave.input_registers[reg.unit_pdu(ch.channel) + offset] = word
        base = reg.status_pdu(ch.channel)
        for i, on in enumerate(ch.status_bits):
            _set_bit(slave.discrete_inputs, base + i, on=on)


def load_cal_state(
    slave: MockSlave, group: int, *, calibrating: bool = True, gas2: bool = False
) -> None:
    """Set ``group``'s cal-group discretes (``11009``-``11016``) on ``slave``.

    Mirrors :meth:`ModbusClient._read_cal_progress`: the group's pair of discretes
    is ``[calibrating, gas2]`` at ``2*(group-1)`` within the cal-group block. With
    ``calibrating=True`` and ``gas2`` selecting cal-gas-1 vs -2, a subsequent
    ``calibration_status(group)`` reports ``active=True`` and the matching
    :class:`CalPhase`.
    """
    from servomexlib.protocol.modbus import registers as reg

    base = reg.analyser_status_pdu(reg.CAL_GROUP_DISCRETE_BASE)
    idx = base + 2 * (group - 1)
    _set_bit(slave.discrete_inputs, idx, on=calibrating)
    _set_bit(slave.discrete_inputs, idx + 1, on=gas2)


@asynccontextmanager
async def mock_modbus_transport(
    *,
    framing: Framing | None = None,
    address: int = 1,
    channels: Sequence[MockChannel] = DEFAULT_4100_BANK,
    disabled_function_codes: frozenset[int] | None = None,
    record_coil_ops: bool = False,
    record_read_ops: bool = False,
    valid_input_ranges: Sequence[tuple[int, int]] | None = None,
    valid_discrete_ranges: Sequence[tuple[int, int]] | None = None,
) -> AsyncGenerator[tuple[Transport, MockSlave]]:
    """Yield ``(Transport, MockSlave)`` — the client-end transport over a serving slave.

    Lower-level than :func:`mock_modbus_pair`: it hands back the raw
    :class:`~servomexlib.transport.serial.SerialTransport` (the no-hardware path
    for the ``AUTO`` ladder and ad-hoc binding) connected to a preloaded byte-
    accurate :class:`MockSlave`. The transport and the slave end are closed on exit.
    """
    import anyio
    from anymodbus import ExceptionCode
    from anymodbus import Framing as _Framing
    from anymodbus.testing import MockSlave as _MockSlave
    from anyserial import SerialConfig
    from anyserial.testing import serial_port_pair

    from servomexlib.transport.base import SerialSettings
    from servomexlib.transport.serial import SerialTransport

    resolved_framing = framing if framing is not None else _Framing.RTU
    config = SerialConfig(baudrate=19200)
    client_end, slave_end = serial_port_pair(config_a=config, config_b=config)
    transport = SerialTransport(client_end, SerialSettings(port="mock://servomex"))
    base_cls = _recording_slave_cls() if record_coil_ops or record_read_ops else _MockSlave

    class RangedMockSlave(base_cls):  # type: ignore[misc, valid-type]
        """A ``MockSlave`` variant that can reject reads crossing invalid gaps."""

        def _handle_request(self, pdu: bytes) -> bytes:
            op = _decode_read_op(pdu)
            if op is not None:
                if (
                    op.function_code == _FC_READ_INPUT_REGISTERS
                    and valid_input_ranges is not None
                    and not _inside_any_range(op.address, op.count, valid_input_ranges)
                ):
                    return bytes((op.function_code | 0x80, int(ExceptionCode.ILLEGAL_DATA_ADDRESS)))
                if (
                    op.function_code == _FC_READ_DISCRETE_INPUTS
                    and valid_discrete_ranges is not None
                    and not _inside_any_range(op.address, op.count, valid_discrete_ranges)
                ):
                    return bytes((op.function_code | 0x80, int(ExceptionCode.ILLEGAL_DATA_ADDRESS)))
            response: bytes = super()._handle_request(pdu)
            return response

    has_range_limits = valid_input_ranges is not None or valid_discrete_ranges is not None
    slave_cls = RangedMockSlave if has_range_limits else base_cls
    slave = slave_cls(
        address=address,
        discrete_input_count=1100,  # cover the analyser-status block at PDU 1000+
        framing=resolved_framing,
        disabled_function_codes=disabled_function_codes,
    )
    load_4100_banks(slave, channels)

    async with anyio.create_task_group() as tg:
        _ = tg.start_soon(slave.serve, slave_end)
        try:
            yield transport, slave
        finally:
            tg.cancel()
            with anyio.CancelScope(shield=True):
                await transport.aclose()
                await slave_end.aclose()


@asynccontextmanager
async def mock_modbus_pair(
    *,
    framing: Framing | None = None,
    address: int = 1,
    channels: Sequence[MockChannel] = DEFAULT_4100_BANK,
    client_channels: Sequence[ChannelId] | None = None,
    device: str = "mock",
    disabled_function_codes: frozenset[int] | None = None,
    record_coil_ops: bool = False,
    record_read_ops: bool = False,
    valid_input_ranges: Sequence[tuple[int, int]] | None = None,
    valid_discrete_ranges: Sequence[tuple[int, int]] | None = None,
) -> AsyncGenerator[tuple[ModbusClient, MockSlave]]:
    """Yield ``(ModbusClient, MockSlave)`` connected over an in-process serial pair.

    The client is the real :class:`~servomexlib.protocol.modbus.client.ModbusClient`
    bound through a real :class:`~servomexlib.protocol.modbus.session.ModbusSession`
    and :class:`~servomexlib.transport.serial.SerialTransport`, so the full encode/
    frame/decode stack is exercised against byte-accurate ADUs. The mock's banks
    may be mutated mid-test; reads observe the change on the next request.
    """
    from servomexlib.protocol.modbus.client import ModbusClient as _ModbusClient
    from servomexlib.protocol.modbus.session import ModbusSession as _ModbusSession

    async with mock_modbus_transport(
        framing=framing,
        address=address,
        channels=channels,
        disabled_function_codes=disabled_function_codes,
        record_coil_ops=record_coil_ops,
        record_read_ops=record_read_ops,
        valid_input_ranges=valid_input_ranges,
        valid_discrete_ranges=valid_discrete_ranges,
    ) as (transport, slave):
        session = _ModbusSession(transport, address=address, framing=slave.framing)
        client = _ModbusClient(session, channels=client_channels, device=device)
        yield client, slave
