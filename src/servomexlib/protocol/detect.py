"""The ``AUTO`` protocol-detection ladder.

Modes are mutually exclusive and a device in continuous mode is silent to Modbus
(and vice-versa), so ``AUTO``:

1. **Drains** input and leaves the transport pushback buffer empty, so the byte
   stream handed to ``anymodbus``'s framer starts clean (single-reader
   discipline).
2. **Probes Modbus** at ``address`` with a cheap FC08 loopback — RTU first, then
   ASCII — short per-try timeout, a couple of tries (RS485 cold-start turnaround).
3. Else **passive-listens** for one checksum-valid continuous frame within a window
   scaled to the frame cadence.
4. Else raises :class:`ServomexConnectionError` (context records what was tried).

Probing Modbus first is cheap (fast request/response, fails fast when silent); the
slow path — waiting out a possibly multi-second continuous cadence — is last.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import anyio

from servomexlib.errors import ErrorContext, ServomexConnectionError, ServomexError
from servomexlib.protocol.base import ProtocolKind
from servomexlib.protocol.continuous import checksum
from servomexlib.protocol.continuous.parser import parse_frame

if TYPE_CHECKING:
    from servomexlib.transport.base import Transport

#: Default per-try Modbus-probe timeout (seconds).
DEFAULT_PROBE_TIMEOUT = 0.5
#: Default Modbus-probe try count (RS485 cold-start turnaround).
DEFAULT_PROBE_TRIES = 2
#: Default continuous passive-listen window (seconds).
DEFAULT_LISTEN_TIMEOUT = 5.0
_CRLF = b"\r\n"
_LOOPBACK_PAYLOAD = b"\xab\xcd"


async def detect_protocol(
    transport: Transport,
    *,
    address: int = 1,
    probe_timeout: float = DEFAULT_PROBE_TIMEOUT,
    probe_tries: int = DEFAULT_PROBE_TRIES,
    listen_timeout: float = DEFAULT_LISTEN_TIMEOUT,
    frame_frequency: float | None = None,
) -> ProtocolKind:
    """Sniff which protocol ``transport`` is speaking.

    Args:
        transport: The (already-open) transport, shared across all three modes.
        address: Modbus slave address to probe.
        probe_timeout: Per-try timeout for each Modbus loopback probe.
        probe_tries: How many times to retry each framing's probe.
        listen_timeout: Floor for the continuous passive-listen window.
        frame_frequency: Expected continuous cadence (s); the listen window is
            ``max(2 × frame_frequency, listen_timeout)`` when known.

    Returns:
        The resolved :class:`ProtocolKind` (never ``AUTO``).

    Raises:
        ServomexConnectionError: No recognised protocol responded.
    """
    await transport.drain_input()

    for framing_kind in (ProtocolKind.MODBUS_RTU, ProtocolKind.MODBUS_ASCII):
        if await _probe_modbus(transport, framing_kind, address, probe_timeout, probe_tries):
            return framing_kind
        await transport.drain_input()

    if await _listen_continuous(transport, listen_timeout, frame_frequency):
        return ProtocolKind.CONTINUOUS_ASCII

    raise ServomexConnectionError(
        "no recognised protocol on AUTO probe (tried Modbus RTU/ASCII loopback "
        "and continuous passive-listen)",
        context=ErrorContext(
            port=transport.label,
            address=address,
            protocol=ProtocolKind.AUTO,
        ),
    )


async def _probe_modbus(
    transport: Transport,
    kind: ProtocolKind,
    address: int,
    probe_timeout: float,
    probe_tries: int,
) -> bool:
    from anymodbus import Framing

    from servomexlib.protocol.modbus.session import ModbusSession

    framing = Framing.RTU if kind is ProtocolKind.MODBUS_RTU else Framing.ASCII
    session = ModbusSession(
        transport, address=address, framing=framing, request_timeout=probe_timeout
    )
    for _ in range(probe_tries):
        try:
            echoed = await session.slave.diagnostic_loopback(_LOOPBACK_PAYLOAD)
        except Exception:  # noqa: S112 — any framing/CRC/timeout error just means "not this mode"
            continue
        if echoed == _LOOPBACK_PAYLOAD:
            return True
    return False


async def _listen_continuous(
    transport: Transport,
    listen_timeout: float,
    frame_frequency: float | None,
) -> bool:
    window = listen_timeout
    if frame_frequency is not None:
        window = max(2 * frame_frequency, listen_timeout)
    # The first read after draining may land mid-frame, so resync: keep reading
    # frames until one parses cleanly or the window expires.
    found = False
    with anyio.move_on_after(window):
        while not found:
            try:
                raw = await transport.read_until(_CRLF, timeout=window)
            except ServomexError:
                break
            try:
                checksum.verify(raw.rstrip(_CRLF))
                parse_frame(raw)
            except ServomexError:
                continue  # misaligned/partial frame — try the next one
            found = True
    return found


__all__ = [
    "DEFAULT_LISTEN_TIMEOUT",
    "DEFAULT_PROBE_TIMEOUT",
    "DEFAULT_PROBE_TRIES",
    "detect_protocol",
]
