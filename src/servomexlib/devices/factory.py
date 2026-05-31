"""``open_device`` — THE entry point.

A single free async function builds the right client for the requested (or sniffed)
protocol over a shared transport and wraps it in an :class:`Analyzer`. ``port``
accepts a ``str`` **or** a pre-built :class:`Transport` (a ``FakeTransport`` is the
no-hardware path). With ``identify=True`` the analyzer caches :class:`DeviceInfo`
on entry (continuous mode waits for the first frame).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from servomexlib.devices.analyzer import Analyzer
from servomexlib.protocol.base import ProtocolKind
from servomexlib.protocol.continuous.client import ContinuousClient

if TYPE_CHECKING:
    from servomexlib.protocol.base import ProtocolClient
    from servomexlib.transport.base import SerialSettings, Transport

_MODBUS_KINDS = (ProtocolKind.MODBUS_RTU, ProtocolKind.MODBUS_ASCII)


async def open_device(
    port: str | Transport,
    *,
    protocol: ProtocolKind = ProtocolKind.AUTO,
    address: int = 1,
    serial_settings: SerialSettings | None = None,
    timeout: float = 1.0,
    identify: bool = True,
) -> Analyzer:
    """Open an analyser and return a (not-yet-entered) :class:`Analyzer`.

    Args:
        port: Serial path (``"COM11"``) or a pre-built transport.
        protocol: Wire protocol, or ``AUTO`` to sniff.
        address: Modbus slave address (RS485 multidrop).
        serial_settings: Overrides the default ``19200 / 8-N-1`` for a ``str`` port.
        timeout: Default per-call/first-frame timeout.
        identify: Cache :class:`DeviceInfo` on ``__aenter__`` (continuous waits for
            the first frame).

    Returns:
        An :class:`Analyzer`; enter it as an async context manager to start I/O.
    """
    transport = await resolve_transport(port, serial_settings)
    resolved = protocol
    if resolved is ProtocolKind.AUTO:
        from servomexlib.protocol.detect import detect_protocol

        resolved = await detect_protocol(
            transport, address=address, listen_timeout=max(timeout, 2.0)
        )
    client = build_client(transport, resolved, address=address, timeout=timeout)
    return Analyzer(client, device=transport.label, identify_on_enter=identify)


async def open_continuous(
    port: str | Transport,
    *,
    device: str = "",
    timeout: float = 1.0,
    serial_settings: SerialSettings | None = None,
) -> Analyzer:
    """Build a continuous-mode :class:`Analyzer` (explicit, no sniff).

    A thin convenience over :func:`open_device` with ``protocol=CONTINUOUS_ASCII``
    and ``identify=False`` — kept for callers and tests that want the continuous
    path without the AUTO ladder.
    """
    transport = await resolve_transport(port, serial_settings)
    label = device or transport.label
    client = ContinuousClient(transport, device=label, timeout=timeout)
    return Analyzer(client, device=label)


def build_client(
    transport: Transport,
    protocol: ProtocolKind,
    *,
    address: int,
    timeout: float,
) -> ProtocolClient:
    if protocol is ProtocolKind.CONTINUOUS_ASCII:
        return ContinuousClient(transport, device=transport.label, timeout=timeout)
    if protocol in _MODBUS_KINDS:
        from anymodbus import Framing

        from servomexlib.protocol.modbus.client import ModbusClient
        from servomexlib.protocol.modbus.session import ModbusSession

        framing = Framing.RTU if protocol is ProtocolKind.MODBUS_RTU else Framing.ASCII
        session = ModbusSession(
            transport, address=address, framing=framing, request_timeout=timeout
        )
        return ModbusClient(session, device=transport.label)
    msg = f"cannot build a client for protocol {protocol!r}"  # pragma: no cover
    raise ValueError(msg)


async def resolve_transport(
    port: str | Transport,
    serial_settings: SerialSettings | None,
) -> Transport:
    if isinstance(port, str):
        from servomexlib.transport.base import SerialSettings as _Settings
        from servomexlib.transport.serial import SerialTransport

        return await SerialTransport.open(serial_settings or _Settings(port=port))
    return port


__all__ = ["open_continuous", "open_device"]
