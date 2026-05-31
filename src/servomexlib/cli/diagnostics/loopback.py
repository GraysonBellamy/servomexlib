"""``servomex-diag loopback`` — FC08 sub-0 echo probe (Modbus, read-only).

Sends a Modbus diagnostic loopback (FC08 sub-function 0) and checks the device
echoes the payload — the cheapest "is the slave alive?" check.
Requires a real Modbus link (the byte-accurate fake is exercised in unit tests).
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from servomexlib.cli._common import hex_tokens_to_bytes
from servomexlib.errors import ServomexProtocolUnsupportedError
from servomexlib.protocol.base import ProtocolKind

if TYPE_CHECKING:
    import argparse

__all__ = ["run"]


async def run(args: argparse.Namespace) -> int:
    """Open a Modbus link and echo ``--payload`` via FC08 sub-0."""
    from servomexlib.devices.factory import build_client, resolve_transport  # noqa: PLC0415

    payload = hex_tokens_to_bytes(args.payload) if args.payload else b"\xab\xcd"
    protocol = ProtocolKind(args.protocol)
    if protocol not in (ProtocolKind.MODBUS_RTU, ProtocolKind.MODBUS_ASCII):
        protocol = ProtocolKind.MODBUS_RTU  # loopback is a Modbus-only probe

    transport = await resolve_transport(args.port, None)
    client = build_client(transport, protocol, address=args.address, timeout=args.timeout)
    loopback = getattr(client, "loopback", None)
    if loopback is None:
        raise ServomexProtocolUnsupportedError("loopback requires a Modbus protocol")
    try:
        echo = await loopback(payload, timeout=args.timeout)
    finally:
        await client.aclose()

    ok = echo == payload
    sys.stdout.write(f"loopback sent={payload.hex()} echo={echo.hex()} ok={ok}\n")
    return 0 if ok else 1
