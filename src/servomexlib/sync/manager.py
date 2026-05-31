"""Synchronous mirror of :class:`~servomexlib.manager.ServomexManager`.

:class:`SyncManager` wraps the async manager through a
:class:`~servomexlib.sync.portal.SyncPortal`, exposing blocking ``add`` / ``remove``
/ ``poll`` / ``poll_samples`` for scripts and notebooks. ``get`` stays synchronous.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Self

from servomexlib.manager import ErrorPolicy, ServomexManager
from servomexlib.protocol.base import ProtocolKind
from servomexlib.sync.portal import SyncPortal

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from types import TracebackType

    from servomexlib.devices.analyzer import Analyzer
    from servomexlib.devices.models import Frame
    from servomexlib.manager import DeviceResult
    from servomexlib.streaming.sample import Sample
    from servomexlib.transport.base import SerialSettings, Transport

__all__ = ["SyncManager"]


class SyncManager:
    """Blocking facade over :class:`~servomexlib.manager.ServomexManager`."""

    def __init__(
        self,
        *,
        error_policy: ErrorPolicy = ErrorPolicy.RAISE,
        backend: str = "asyncio",
        portal: SyncPortal | None = None,
    ) -> None:
        self._owns_portal = portal is None
        self._portal = portal if portal is not None else SyncPortal(backend=backend)
        self._manager = ServomexManager(error_policy=error_policy)

    def __enter__(self) -> Self:
        if self._owns_portal:
            self._portal.__enter__()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            self._portal.call(self._manager.close)
        finally:
            if self._owns_portal:
                self._portal.__exit__(exc_type, exc, tb)

    @property
    def names(self) -> tuple[str, ...]:
        """Insertion-ordered tuple of managed analyser names."""
        return self._manager.names

    @property
    def closed(self) -> bool:
        """``True`` once the underlying manager is closed."""
        return self._manager.closed

    def add(
        self,
        name: str,
        source: Analyzer | str | Transport,
        *,
        protocol: ProtocolKind | str = ProtocolKind.MODBUS_RTU,
        address: int = 1,
        serial_settings: SerialSettings | None = None,
        timeout: float = 1.0,
    ) -> Analyzer:
        """Register an analyser; returns the underlying async :class:`Analyzer`."""
        return self._portal.call(
            self._manager.add,
            name,
            source,
            protocol=ProtocolKind(protocol),
            address=address,
            serial_settings=serial_settings,
            timeout=timeout,
        )

    def remove(self, name: str) -> None:
        """Unregister ``name``, closing the shared transport on the last ref."""
        self._portal.call(self._manager.remove, name)

    def get(self, name: str) -> Analyzer:
        """Return the analyser registered under ``name`` (no I/O)."""
        return self._manager.get(name)

    def poll_samples(
        self, *, names: Sequence[str] | None = None, timeout: float | None = None
    ) -> list[Sample]:
        """Poll every (or named) analyser → flat samples."""
        return self._portal.call(self._manager.poll_samples, names=names, timeout=timeout)

    def poll(
        self, names: Sequence[str] | None = None, *, timeout: float | None = None
    ) -> Mapping[str, DeviceResult[Frame]]:
        """Read one :class:`Frame` per (or named) analyser, keyed by name."""
        return self._portal.call(self._manager.poll, names, timeout=timeout)
