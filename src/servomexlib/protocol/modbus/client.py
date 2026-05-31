"""The Modbus semantic client — the ~5 device operations.

A thin client over an :class:`~servomexlib.protocol.modbus.session.ModbusSession`
(an ``anymodbus`` slave bound to our transport). It implements the uniform
:class:`~servomexlib.protocol.base.ProtocolClient` read surface plus the
:class:`~servomexlib.protocol.base.CalibrationControl` ops, decoding registers
into the **same** models the continuous parser produces.

Wire facts (HW-confirmed, memory ``servomex-modbus-validation``): measurement
values are IEEE-754 float32, high word first (``WordOrder.HIGH_LOW`` +
``ByteOrder.BIG``), read as input registers (FC04). Names (3 regs) and units
(2 regs, trailing NUL) come back in the analyser's display ROM — **not ASCII** —
so they are read as raw registers and routed through
:func:`~servomexlib.registry.charset.decode_display`, never ``anymodbus``'s ASCII
string helper. Per-channel status is FC02 (stride 8); the analyser-status block is
FC02 at ``11001``.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import anymodbus
from anymodbus import ByteOrder, Framing, WordOrder
from anymodbus.decoders import decode_float32

from servomexlib.devices.capability import Capability
from servomexlib.devices.models import (
    CalibrationProgress,
    CalPhase,
    ChannelInfo,
    DeviceInfo,
    Frame,
    Reading,
)
from servomexlib.errors import ErrorContext, ServomexError
from servomexlib.protocol.base import ProtocolKind
from servomexlib.protocol.modbus import registers as reg
from servomexlib.protocol.modbus.errors import remap_modbus_exception
from servomexlib.protocol.modbus.read_plan import (
    DEFAULT_READ_POLICY,
    INPUT_CHANNEL_REGISTERS,
    BlockRead,
    InputChannelSlice,
    ReadCoalescingPolicy,
    StatusChannelSlice,
    plan_input_reads,
    plan_status_reads,
)
from servomexlib.registry.channels import (
    CHANNEL_SPECS,
    NAME_REGISTERS,
    UNIT_REGISTERS,
    VALUE_REGISTERS,
    ChannelId,
    kind_for,
)
from servomexlib.registry.charset import decode_display
from servomexlib.registry.status import decode_analyser_status, decode_discrete_status
from servomexlib.registry.units import Unit, coerce_unit
from servomexlib.transport.base import SerialSettings

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from servomexlib.devices.models import AnalyserStatus, ChannelStatus
    from servomexlib.protocol.modbus.session import ModbusSession

_KIND_BY_FRAMING = {
    Framing.RTU: ProtocolKind.MODBUS_RTU,
    Framing.ASCII: ProtocolKind.MODBUS_ASCII,
}
#: Discretes covering analyser fault/maintenance (11001/11002) + the 8 cal-group
#: flags (11009-11016) in one FC02 read.
_ANALYSER_BLOCK = 16


def _words_to_bytes(words: Sequence[int]) -> bytes:
    """Pack 16-bit registers big-endian into a byte string (for charset decode)."""
    out = bytearray()
    for word in words:
        out += int(word).to_bytes(2, "big")
    return bytes(out)


def _decode_name(words: Sequence[int]) -> str | None:
    text = decode_display(_words_to_bytes(words)).rstrip(" \x00")
    if not text or set(text) <= {"|"}:
        return None
    return text


@dataclass(frozen=True, slots=True)
class _ChannelMetadata:
    name: str | None
    unit: Unit
    raw_name: bytes


class ModbusClient:
    """Polled Modbus client — one FC04+FC02 sweep per :meth:`read_frame`."""

    capabilities = (
        Capability.READ_CHANNELS
        | Capability.READ_STATUS
        | Capability.IDENTIFY
        | Capability.AUTOCAL
        | Capability.LOOPBACK
    )

    def __init__(
        self,
        session: ModbusSession,
        *,
        channels: Sequence[ChannelId] | None = None,
        device: str = "",
        read_policy: ReadCoalescingPolicy | None = None,
    ) -> None:
        self._session = session
        self._device = device
        self._channels: tuple[ChannelId, ...] = (
            tuple(channels) if channels is not None else tuple(CHANNEL_SPECS)
        )
        self._read_policy = read_policy if read_policy is not None else DEFAULT_READ_POLICY
        self._metadata: dict[ChannelId, _ChannelMetadata] = {}
        self._input_plan: tuple[BlockRead[InputChannelSlice], ...] = ()
        self._status_plan: tuple[BlockRead[StatusChannelSlice], ...] = ()
        self._planned_channels: tuple[ChannelId, ...] | None = None
        self._planned_policy: ReadCoalescingPolicy | None = None

    @property
    def kind(self) -> ProtocolKind:
        """``MODBUS_RTU`` or ``MODBUS_ASCII`` per the session framing."""
        return _KIND_BY_FRAMING[self._session.framing]

    @property
    def channels(self) -> tuple[ChannelId, ...]:
        """The channel slots this client sweeps (refined by ``identify``)."""
        return self._channels

    @property
    def read_policy(self) -> ReadCoalescingPolicy:
        """The currently learned read-coalescing policy."""
        return self._read_policy

    def set_channels(self, channels: Sequence[ChannelId]) -> None:
        """Narrow the swept set to the populated slots (called from ``identify``)."""
        self._channels = tuple(channels)
        self._invalidate_plan()

    # ------------------------------------------------------------------ public reads

    async def read_frame(
        self,
        *,
        wait_fresh: bool = False,
        timeout: float | None = None,
    ) -> Frame:
        """Sweep every populated slot (FC04) + status (FC02) into one :class:`Frame`.

        ``wait_fresh`` is accepted for interface parity with the continuous client
        and ignored: every Modbus sweep is a fresh request/response.
        """
        return await self._execute(self._sweep, timeout=timeout, context=self._ctx())

    async def read_channel(self, channel: ChannelId, *, timeout: float | None = None) -> Reading:
        """Targeted 7-register + 8-discrete read of one channel."""
        return await self._execute(
            lambda: self._read_reading(channel, *self._now()),
            timeout=timeout,
            context=self._ctx(channel=channel),
        )

    async def status(self, channel: ChannelId, *, timeout: float | None = None) -> ChannelStatus:
        """Read one channel's FC02 status bitmap."""
        return await self._execute(
            lambda: self._read_status(channel),
            timeout=timeout,
            context=self._ctx(channel=channel),
        )

    async def analyser_status(self, *, timeout: float | None = None) -> AnalyserStatus:
        """Read the analyser-level status block (FC02 at 11001)."""
        return await self._execute(self._read_analyser, timeout=timeout, context=self._ctx())

    async def identify(self, *, timeout: float | None = None) -> DeviceInfo:
        """Read name/unit per slot; report the populated channels."""
        return await self._execute(self._identify, timeout=timeout, context=self._ctx())

    async def loopback(
        self, payload: bytes = b"\x00\x00", *, timeout: float | None = None
    ) -> bytes:
        """FC08 sub-0 diagnostic loopback — echoes ``payload`` (AUTO probe / diag)."""
        return await self._execute(
            lambda: self._session.slave.diagnostic_loopback(payload),
            timeout=timeout,
            context=self._ctx(function_code=0x08),
        )

    # ------------------------------------------------------------------ control (I/O only)

    async def start_calibration(self, group: int, *, timeout: float | None = None) -> None:
        """Pulse the cal-group start coil 0→1→0 (FC05, readback FC01)."""
        coil = reg.cal_group_start_coil_pdu(group)
        await self._execute(
            lambda: self._pulse_coil(coil),
            timeout=timeout,
            context=self._ctx(register=reg.CAL_GROUP_START_COILS[group], function_code=0x05),
        )

    async def stop_calibration(self, *, timeout: float | None = None) -> None:
        """Pulse the stop-all coil 0→1→0 (FC05)."""
        coil = reg.stop_all_coil_pdu()
        await self._execute(
            lambda: self._pulse_coil(coil),
            timeout=timeout,
            context=self._ctx(register=reg.STOP_ALL_COIL, function_code=0x05),
        )

    async def calibration_status(
        self, group: int = 1, *, timeout: float | None = None
    ) -> CalibrationProgress:
        """Read the cal-group discretes (11009-11016) → :class:`CalibrationProgress`."""
        return await self._execute(
            lambda: self._read_cal_progress(group),
            timeout=timeout,
            context=self._ctx(),
        )

    async def aclose(self) -> None:
        """Close the bound transport."""
        await self._session.aclose()

    # ------------------------------------------------------------------ low-level (no lock)

    async def _sweep(self) -> Frame:
        received_at, monotonic_ns = self._now()
        return await self._with_plan_fallback(lambda: self._sweep_once(received_at, monotonic_ns))

    async def _sweep_once(self, received_at: datetime, monotonic_ns: int) -> Frame:
        input_words = await self._read_input_channel_words(self._channels)
        statuses = await self._read_status_map(self._channels)
        readings = tuple(
            self._build_reading(
                cid,
                input_words[cid],
                statuses[cid],
                received_at,
                monotonic_ns,
                refresh_metadata=False,
            )
            for cid in self._channels
        )
        analyser = await self._read_analyser()
        return Frame(
            readings=readings,
            analyser=analyser,
            protocol=self.kind,
            received_at=received_at,
            monotonic_ns=monotonic_ns,
            raw=b"",
        )

    async def _read_reading(
        self, channel: ChannelId, received_at: datetime, monotonic_ns: int
    ) -> Reading:
        return await self._with_plan_fallback(
            lambda: self._read_reading_once(channel, received_at, monotonic_ns)
        )

    async def _read_reading_once(
        self, channel: ChannelId, received_at: datetime, monotonic_ns: int
    ) -> Reading:
        words = (await self._read_input_channel_words((channel,)))[channel]
        status = (await self._read_status_map((channel,)))[channel]
        return self._build_reading(
            channel,
            words,
            status,
            received_at,
            monotonic_ns,
            refresh_metadata=False,
        )

    async def _read_status(self, channel: ChannelId) -> ChannelStatus:
        return (await self._read_status_map((channel,)))[channel]

    async def _read_analyser(self) -> AnalyserStatus:
        base = reg.analyser_status_pdu(reg.ANALYSER_FAULT_DISCRETE)
        bits = await self._session.slave.read_discrete_inputs(base, count=_ANALYSER_BLOCK)
        # bits[0]=11001 fault, bits[1]=11002 maintenance, bits[8:16]=11009-11016 cal groups.
        return decode_analyser_status(
            fault=bits[0],
            maintenance=bits[1],
            cal_group_bits=bits[8:_ANALYSER_BLOCK],
        )

    async def _read_cal_progress(self, group: int) -> CalibrationProgress:
        base = reg.analyser_status_pdu(reg.CAL_GROUP_DISCRETE_BASE)
        bits = await self._session.slave.read_discrete_inputs(
            base, count=reg.CAL_GROUP_DISCRETE_COUNT
        )
        idx = 2 * (group - 1)
        calibrating = idx < len(bits) and bool(bits[idx])
        gas2 = idx + 1 < len(bits) and bool(bits[idx + 1])
        if not calibrating:
            phase = CalPhase.IDLE
        else:
            phase = CalPhase.CAL_GAS_2 if gas2 else CalPhase.CAL_GAS_1
        return CalibrationProgress(group=group, active=calibrating, phase=phase)

    async def _identify(self) -> DeviceInfo:
        return await self._with_plan_fallback(self._identify_once)

    async def _identify_once(self) -> DeviceInfo:
        infos: list[ChannelInfo] = []
        populated: list[ChannelId] = []
        input_words = await self._read_input_channel_words(tuple(CHANNEL_SPECS))
        for cid in tuple(CHANNEL_SPECS):
            metadata = self._metadata_from_words(input_words[cid])
            self._metadata[cid] = metadata
            if metadata.name is None:
                continue
            infos.append(
                ChannelInfo(
                    channel=cid,
                    kind=kind_for(cid),
                    name=metadata.name,
                    unit=metadata.unit,
                )
            )
            populated.append(cid)
        if populated:
            self.set_channels(populated)
        return DeviceInfo(
            model="4000-series",
            channels=tuple(infos),
            protocol=self.kind,
            address=self._session.address,
            serial_settings=SerialSettings(port=self._session.transport.label),
        )

    async def _read_input_channel_words(
        self, channels: Sequence[ChannelId]
    ) -> dict[ChannelId, tuple[int, ...]]:
        values: dict[ChannelId, tuple[int, ...]] = {}
        for block in self._input_plan_for(tuple(channels)):
            words = await self._session.slave.read_input_registers(block.base, count=block.count)
            for block_slice in block.slices:
                start = block_slice.value_offset
                values[block_slice.channel] = tuple(words[start : start + INPUT_CHANNEL_REGISTERS])
        return values

    async def _read_status_map(
        self, channels: Sequence[ChannelId]
    ) -> dict[ChannelId, ChannelStatus]:
        statuses: dict[ChannelId, ChannelStatus] = {}
        for block in self._status_plan_for(tuple(channels)):
            bits = await self._session.slave.read_discrete_inputs(block.base, count=block.count)
            for block_slice in block.slices:
                channel_bits = bits[block_slice.offset : block_slice.offset + 8]
                statuses[block_slice.channel] = decode_discrete_status(
                    channel_bits,
                    kind_for(block_slice.channel),
                )
        return statuses

    def _build_reading(
        self,
        channel: ChannelId,
        words: Sequence[int],
        status: ChannelStatus,
        received_at: datetime,
        monotonic_ns: int,
        *,
        refresh_metadata: bool,
    ) -> Reading:
        if refresh_metadata or channel not in self._metadata:
            self._metadata[channel] = self._metadata_from_words(words)
        metadata = self._metadata[channel]
        value = decode_float32(
            words[:VALUE_REGISTERS],
            word_order=WordOrder.HIGH_LOW,
            byte_order=ByteOrder.BIG,
        )
        return Reading(
            channel=channel,
            kind=kind_for(channel),
            name=metadata.name,
            value=None if math.isnan(value) else value,
            unit=metadata.unit,
            status=status,
            protocol=self.kind,
            received_at=received_at,
            monotonic_ns=monotonic_ns,
            raw=metadata.raw_name,
        )

    def _metadata_from_words(self, words: Sequence[int]) -> _ChannelMetadata:
        name_words = words[VALUE_REGISTERS : VALUE_REGISTERS + NAME_REGISTERS]
        unit_words = words[
            VALUE_REGISTERS + NAME_REGISTERS : VALUE_REGISTERS + NAME_REGISTERS + UNIT_REGISTERS
        ]
        return _ChannelMetadata(
            name=_decode_name(name_words),
            unit=coerce_unit(decode_display(_words_to_bytes(unit_words), strip_null=True)),
            raw_name=_words_to_bytes(name_words),
        )

    def _input_plan_for(
        self,
        channels: tuple[ChannelId, ...],
    ) -> tuple[BlockRead[InputChannelSlice], ...]:
        if channels == self._channels:
            self._ensure_sweep_plan()
            return self._input_plan
        return plan_input_reads(channels, self._read_policy)

    def _status_plan_for(
        self,
        channels: tuple[ChannelId, ...],
    ) -> tuple[BlockRead[StatusChannelSlice], ...]:
        if channels == self._channels:
            self._ensure_sweep_plan()
            return self._status_plan
        return plan_status_reads(channels, self._read_policy)

    def _ensure_sweep_plan(self) -> None:
        if self._planned_channels == self._channels and self._planned_policy == self._read_policy:
            return
        self._input_plan = plan_input_reads(self._channels, self._read_policy)
        self._status_plan = plan_status_reads(self._channels, self._read_policy)
        self._planned_channels = self._channels
        self._planned_policy = self._read_policy

    def _invalidate_plan(self) -> None:
        self._input_plan = ()
        self._status_plan = ()
        self._planned_channels = None
        self._planned_policy = None

    async def _with_plan_fallback[T](self, fn: Callable[[], Awaitable[T]]) -> T:
        try:
            return await fn()
        except Exception as exc:
            if not self._should_fallback(exc):
                raise
            self._read_policy = self._read_policy.strict()
            self._invalidate_plan()
            return await fn()

    def _should_fallback(self, exc: Exception) -> bool:
        if not self._read_policy.fallback or self._read_policy.is_strict:
            return False
        return isinstance(exc, (anymodbus.IllegalDataAddressError, anymodbus.FrameTimeoutError))

    async def _pulse_coil(self, coil: int) -> None:
        slave = self._session.slave
        await slave.write_coil(coil, on=True)
        await slave.read_coils(coil, count=1)  # readback (FC01) per the 0→1 contract
        await slave.write_coil(coil, on=False)

    # ------------------------------------------------------------------ plumbing

    async def _execute[T](
        self,
        fn: Callable[[], Awaitable[T]],
        *,
        timeout: float | None,
        context: ErrorContext,
    ) -> T:
        # ``anymodbus`` owns transaction timing: ``request_timeout`` bounds each
        # attempt and the ``RetryPolicy`` re-issues transient failures, raising a
        # mapped timeout on exhaustion. A high-level op may issue *many*
        # transactions (a frame sweep reads value/name/unit/status per channel),
        # so there is no single meaningful outer deadline to impose — wrapping the
        # whole op in one ``fail_after`` would just cancel a legitimate mid-sweep
        # retry. Per-call ``timeout`` is therefore not an outer guard here; bus
        # timing (set at open) is the authority.
        del timeout
        async with self._session.lock:
            try:
                return await fn()
            except ServomexError:
                raise
            except Exception as exc:  # anymodbus engine exception → mapped at the boundary
                raise remap_modbus_exception(exc, context=context) from exc

    def _now(self) -> tuple[datetime, int]:
        return datetime.now(UTC), time.monotonic_ns()

    def _ctx(self, **updates: object) -> ErrorContext:
        return ErrorContext(
            port=self._session.transport.label,
            protocol=self.kind,
            address=self._session.address,
            **updates,  # type: ignore[arg-type]
        )


__all__ = ["ModbusClient"]
