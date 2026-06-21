"""Read-only device discovery.

:func:`discover_port` probes one port (or pre-built transport) with the ``AUTO``
ladder and, on success, a single ``identify`` — never writing anything stateful.
:func:`find_devices` sweeps several ports and folds the attempts into a summary.
The continuous-broadcaster vs Modbus-peer distinction is surfaced through the
resolved :class:`ProtocolKind`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from servomexlib.errors import ServomexError
from servomexlib.protocol.base import ProtocolKind

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine, Sequence

    from servomexlib.devices.models import DeviceInfo
    from servomexlib.transport.base import SerialSettings, Transport


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    """The outcome of probing one port."""

    port: str
    protocol: ProtocolKind | None
    info: DeviceInfo | None
    error: ServomexError | None = None

    @property
    def ok(self) -> bool:
        """Whether a device was recognised on this port."""
        return self.error is None and self.protocol is not None

    @property
    def is_broadcaster(self) -> bool:
        """Whether the device is a continuous-ASCII broadcaster (not an addressable peer)."""
        return self.protocol is ProtocolKind.CONTINUOUS_ASCII


@dataclass(frozen=True, slots=True)
class DiscoverySummary:
    """A folded view over a batch of :func:`discover_port` attempts."""

    found: tuple[DiscoveryResult, ...]
    failed: tuple[DiscoveryResult, ...]

    @property
    def total(self) -> int:
        """How many ports were probed."""
        return len(self.found) + len(self.failed)


async def discover_port(
    port: str | Transport,
    *,
    address: int = 1,
    timeout: float = 1.0,
    serial_settings: SerialSettings | None = None,
) -> DiscoveryResult:
    """Probe one port and return what (if anything) answered.

    Read-only: runs the ``AUTO`` ladder then one ``identify``; closes the transport
    before returning. Failures are captured in :attr:`DiscoveryResult.error`, not
    raised, so a sweep never aborts on one dead port.
    """
    from servomexlib.devices.factory import resolve_transport
    from servomexlib.protocol.detect import detect_protocol

    # We own (and must close) a transport we opened from a str path; a caller-
    # provided Transport stays the caller's to manage.
    owns_transport = isinstance(port, str)
    label = port if isinstance(port, str) else port.label
    transport: Transport | None = None
    try:
        transport = await resolve_transport(port, serial_settings)
        label = transport.label
        protocol = await detect_protocol(
            transport, address=address, listen_timeout=max(timeout, 2.0)
        )
        info = await _identify(transport, protocol, address=address, timeout=timeout)
    except ServomexError as exc:
        return DiscoveryResult(port=label, protocol=None, info=None, error=exc)
    finally:
        if transport is not None and owns_transport:
            await transport.aclose()
    return DiscoveryResult(port=label, protocol=protocol, info=info)


async def find_devices(
    ports: Sequence[str | Transport],
    *,
    address: int = 1,
    timeout: float = 1.0,
) -> list[DiscoveryResult]:
    """Probe each port in turn and return one :class:`DiscoveryResult` per port."""
    return [await discover_port(port, address=address, timeout=timeout) for port in ports]


def summarize(results: Sequence[DiscoveryResult]) -> DiscoverySummary:
    """Fold discovery results into a :class:`DiscoverySummary`."""
    found = tuple(r for r in results if r.ok)
    failed = tuple(r for r in results if not r.ok)
    return DiscoverySummary(found=found, failed=failed)


async def _identify(
    transport: Transport,
    protocol: ProtocolKind,
    *,
    address: int,
    timeout: float,
) -> DeviceInfo:
    from servomexlib.devices.factory import build_client

    client = build_client(transport, protocol, address=address, timeout=timeout)
    run = getattr(client, "run", None)
    if not callable(run):  # Modbus: no background loop needed for a one-shot identify
        return await client.identify(timeout=timeout)
    # Continuous: spin the receive loop just long enough to catch the first frame.
    import anyio

    background = cast("Callable[[], Coroutine[Any, Any, None]]", run)
    async with anyio.create_task_group() as tg:
        _ = tg.start_soon(background)
        info = await client.identify(timeout=timeout)
        tg.cancel()
        return info
    raise AssertionError("unreachable")  # pragma: no cover - the async with always returns/raises


__all__ = [
    "DiscoveryResult",
    "DiscoverySummary",
    "discover_port",
    "find_devices",
    "summarize",
]
