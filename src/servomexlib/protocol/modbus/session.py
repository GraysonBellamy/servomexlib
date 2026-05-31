"""Bind ``anymodbus`` to *our* :class:`Transport`.

This is the deliberate divergence from ``watlowlib``. Watlow lets ``anymodbus``
own the serial port (``open_modbus_rtu(path)``); **we own it** because the 4100
has three mutually-exclusive modes on one port and the ``AUTO`` ladder must sniff
raw bytes before it knows the protocol. So we use the stream-bound ``Bus``
constructor — ``anymodbus.Bus(our_transport, framing=…)`` — and hand it the exact
same :class:`Transport` (or :class:`FakeTransport`) that backs the continuous path.

Single-reader discipline: once a stream is committed to Modbus, every
read goes through the ``anymodbus`` framer and never the continuous pushback
buffer. A per-session :class:`anyio.Lock` serialises transactions so a manager
sharing one port cannot interleave two slaves' frames.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import anyio
from anymodbus import Bus, BusConfig, Framing, RetryPolicy, TimingConfig

if TYPE_CHECKING:
    from anyio.abc import ByteStream
    from anymodbus import Slave

    from servomexlib.transport.base import Transport

#: Inter-frame idle gap between Modbus transactions, in seconds. **Device fact**
#: (bench 4100D, 2026-05-30): the analyser/USB-RS485 link drops ~25% of requests
#: hammered back-to-back at the RTU-spec t3.5 (~2 ms at 19200); a measured sweep
#: showed the drop rate falling to 8% at 10 ms and **0% at 50 ms**. The 4000-series
#: simply needs a far larger inter-frame gap than the spec minimum, so we override
#: ``anymodbus``'s ``"auto"`` (= t3.5) default with a fixed, generous value. This
#: is *the* fix for the intermittent "no response" sweeps — not timeout tuning.
_DEFAULT_INTER_FRAME_IDLE_S = 0.05
#: Additional attempts after the first on a transient (no-response / checksum)
#: error. With the 50 ms gap drops are ~0, but a couple of retries cheaply absorb
#: the rare straggler so a multi-read sweep never fails on one unlucky frame.
_DEFAULT_RETRIES = 2


class ModbusSession:
    """An ``anymodbus`` :class:`~anymodbus.Slave` bound to our transport, plus a lock.

    Timing is owned by ``anymodbus``: ``request_timeout`` bounds each attempt and
    ``retries`` re-issues a transient failure. The crucial device-fit knob is
    ``inter_frame_idle`` — the 4000-series needs ~50 ms of bus silence between
    transactions (see :data:`_DEFAULT_INTER_FRAME_IDLE_S`); the RTU-spec t3.5 the
    library would otherwise auto-compute is far too short and causes intermittent
    dropped responses on rapid sweeps. The client does **not** wrap calls in an
    extra outer deadline — a single high-level op (e.g. a frame sweep) issues many
    transactions, so the per-transaction bus timing is the right and only authority.
    """

    def __init__(
        self,
        transport: Transport,
        *,
        address: int,
        framing: Framing = Framing.RTU,
        request_timeout: float = 1.0,
        retries: int = _DEFAULT_RETRIES,
        inter_frame_idle: float = _DEFAULT_INTER_FRAME_IDLE_S,
        startup_settle: float = 0.0,
    ) -> None:
        self._transport = transport
        self._address = address
        self._framing = framing
        config = BusConfig(
            request_timeout=request_timeout,
            retries=RetryPolicy(retries=retries),
            timing=TimingConfig(inter_frame_idle=inter_frame_idle, startup_settle=startup_settle),
        )
        # Every Transport satisfies anyio.abc.ByteStream by construction;
        # the cast bridges our structural Protocol to anymodbus's ABC.
        self._bus = Bus(cast("ByteStream", transport), config=config, framing=framing)
        self._slave = self._bus.slave(address)
        self._lock = anyio.Lock()

    @property
    def slave(self) -> Slave:
        """The bound ``anymodbus`` slave for this address."""
        return self._slave

    @property
    def lock(self) -> anyio.Lock:
        """Serialises transactions on the shared stream (single-reader discipline)."""
        return self._lock

    @property
    def address(self) -> int:
        """The Modbus slave address."""
        return self._address

    @property
    def framing(self) -> Framing:
        """RTU or ASCII framing for this session."""
        return self._framing

    @property
    def transport(self) -> Transport:
        """The underlying transport (shared with the AUTO ladder / continuous path)."""
        return self._transport

    async def aclose(self) -> None:
        """Close the underlying transport."""
        await self._transport.aclose()


__all__ = ["ModbusSession"]
