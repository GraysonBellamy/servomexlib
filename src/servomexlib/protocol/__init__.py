"""Protocol layer — the device-fit core.

Houses :class:`~servomexlib.protocol.base.ProtocolKind`, the per-protocol
clients (continuous-ASCII and Modbus), the ``AUTO`` sniff ladder, and the
pure continuous-frame parser/checksum.
"""

from __future__ import annotations
