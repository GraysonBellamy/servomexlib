r"""Multi-analyser orchestrator — :class:`ServomexManager`.

Coordinates many :class:`~servomexlib.devices.analyzer.Analyzer` instances across
one or more serial ports. Operations on **different** ports run concurrently
(:func:`anyio.create_task_group`); operations on the **same** port serialise
through that port's shared lock — the single-reader discipline an RS485 multidrop
segment requires. Port identity is canonicalised so ``COM3`` /
``com3`` / ``\\.\COM3`` collapse to one entry; pre-built transports key on
:func:`id`.

**Modbus only.** Continuous-ASCII is a single unsolicited broadcaster, not an
addressable peer, so the manager refuses to register a continuous device — there
is nothing to multidrop. Per-port clients are ref-counted; the last
:meth:`remove` (or :meth:`close`) tears the shared transport down. A pre-built
:class:`Analyzer` source has no port entry — the caller keeps lifecycle ownership.

The manager satisfies :class:`~servomexlib.streaming.poll_source.PollSource`, so it
drives :func:`~servomexlib.streaming.record` directly.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Self

import anyio

from servomexlib._logging import get_logger
from servomexlib.devices.analyzer import Analyzer
from servomexlib.devices.models import Frame
from servomexlib.errors import (
    ErrorContext,
    ServomexConnectionError,
    ServomexError,
    ServomexValidationError,
)
from servomexlib.protocol.base import ProtocolKind

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence
    from types import TracebackType

    from servomexlib.streaming.sample import Sample
    from servomexlib.transport.base import SerialSettings, Transport

__all__ = ["DeviceResult", "ErrorPolicy", "ServomexManager"]

_logger = get_logger("manager")

_MODBUS_KINDS = (ProtocolKind.MODBUS_RTU, ProtocolKind.MODBUS_ASCII)


class ErrorPolicy(Enum):
    """How the manager surfaces per-device failures.

    Under :attr:`RAISE`, the manager collects every result and — if any failed —
    raises an :class:`ExceptionGroup` after the task group joins. Under
    :attr:`RETURN`, each device yields a :class:`DeviceResult` to inspect.
    """

    RAISE = "raise"
    RETURN = "return"


@dataclass(frozen=True, slots=True)
class DeviceResult[T]:
    """Per-device result — value **or** error, never both."""

    value: T | None
    error: ServomexError | None

    @property
    def ok(self) -> bool:
        """``True`` when the device produced a value (``error is None``)."""
        return self.error is None

    @classmethod
    def success(cls, value: T) -> Self:
        """Build a success result wrapping ``value``."""
        return cls(value=value, error=None)

    @classmethod
    def failure(cls, error: ServomexError) -> Self:
        """Build a failure result wrapping ``error``."""
        return cls(value=None, error=error)


# ---------------------------------------------------------------------------
# Port canonicalization
# ---------------------------------------------------------------------------

_WINDOWS_DEVICE_PREFIX = "\\\\.\\"


def _canonical_port_key(port: str) -> str:
    r"""Collapse equivalent port names to a single key.

    Windows: strips the ``\\.\`` device-namespace prefix and uppercases, so
    ``COM3`` / ``com3`` / ``\\.\COM3`` match. POSIX: resolves symlinks via
    :meth:`Path.resolve`, falling back to the raw string if the path is absent.
    """
    if sys.platform == "win32":
        return port.removeprefix(_WINDOWS_DEVICE_PREFIX).upper()
    else:
        path = Path(port)
        return str(path.resolve(strict=False)) if path.exists() else port


# ---------------------------------------------------------------------------
# Internal tracking structures
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _PortEntry:
    """Ref-counted per-port resources shared across analysers on the bus.

    ``lock`` serialises every transaction on the shared stream so two slaves'
    frames cannot interleave. ``protocol`` locks the port to the wire protocol of
    the first device added.
    """

    key: str
    transport: Transport
    lock: anyio.Lock
    owns_transport: bool
    protocol: ProtocolKind | None = None
    refs: set[str] = field(default_factory=set[str])


@dataclass(slots=True)
class _DeviceEntry:
    """One managed :class:`Analyzer` + its port ref (``None`` = caller-owned)."""

    name: str
    analyzer: Analyzer
    port_key: str | None


class ServomexManager:
    """Coordinator for many analysers across one or more serial ports.

    Usage::

        async with ServomexManager() as mgr:
            await mgr.add("a1", "COM11", address=1)
            await mgr.add("a2", "COM11", address=2)
            samples = await mgr.poll_samples()
    """

    def __init__(self, *, error_policy: ErrorPolicy = ErrorPolicy.RAISE) -> None:
        self._error_policy = error_policy
        self._devices: dict[str, _DeviceEntry] = {}
        self._ports: dict[str, _PortEntry] = {}
        self._state_lock = anyio.Lock()
        self._closed = False

    @property
    def error_policy(self) -> ErrorPolicy:
        """The :class:`ErrorPolicy` this manager was constructed with."""
        return self._error_policy

    @property
    def names(self) -> tuple[str, ...]:
        """Insertion-ordered tuple of managed analyser names."""
        return tuple(self._devices.keys())

    @property
    def closed(self) -> bool:
        """``True`` once :meth:`close` has been called."""
        return self._closed

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        del exc_type, tb
        await self._close(suppress_errors=exc is not None)

    # --------------------------------------------------------------- add/remove

    async def add(
        self,
        name: str,
        source: Analyzer | str | Transport,
        *,
        protocol: ProtocolKind = ProtocolKind.MODBUS_RTU,
        address: int = 1,
        serial_settings: SerialSettings | None = None,
        timeout: float = 1.0,
    ) -> Analyzer:
        """Register an analyser under ``name`` and return it.

        ``source`` discriminates lifecycle ownership: a pre-built :class:`Analyzer`
        (caller-owned, tracked only), a ``str`` port path (the manager opens and
        shares a transport across the bus), or a :class:`Transport` (bound, not
        owned). Continuous-ASCII / ``AUTO`` are refused — the manager is a Modbus
        multidrop coordinator.

        Raises:
            ServomexValidationError: duplicate ``name``, a non-Modbus protocol, or
                a protocol clash with an existing device on the same port.
            ServomexConnectionError: the manager is closed.
        """
        async with self._state_lock:
            self._check_open()
            if name in self._devices:
                raise ServomexValidationError(f"manager: name {name!r} already in use")

            if isinstance(source, Analyzer):
                self._devices[name] = _DeviceEntry(name=name, analyzer=source, port_key=None)
                _logger.info("manager.add device=%s port=prebuilt", name)
                return source

            if protocol not in _MODBUS_KINDS:
                raise ServomexValidationError(
                    f"manager: only Modbus protocols can be grouped (got {protocol.value}); "
                    "a continuous-ASCII analyser is a single broadcaster, not a multidrop peer",
                )

            port_key, port_entry = await self._resolve_port(
                source, protocol=protocol, serial_settings=serial_settings
            )
            analyzer = self._build_analyzer(
                port_entry, name=name, protocol=protocol, address=address, timeout=timeout
            )
            self._devices[name] = _DeviceEntry(name=name, analyzer=analyzer, port_key=port_key)
            port_entry.refs.add(name)
            _logger.info(
                "manager.add device=%s port=%s protocol=%s address=%s",
                name,
                port_key,
                protocol.value,
                address,
            )
            return analyzer

    async def remove(self, name: str) -> None:
        """Unregister ``name``, closing the shared transport on the last ref."""
        async with self._state_lock:
            self._check_open()
            if name not in self._devices:
                raise ServomexValidationError(f"manager: no analyser named {name!r}")
            await self._teardown_device(self._devices.pop(name))
            _logger.info("manager.remove device=%s", name)

    def get(self, name: str) -> Analyzer:
        """Return the analyser registered under ``name``."""
        try:
            return self._devices[name].analyzer
        except KeyError:
            raise ServomexValidationError(f"manager: no analyser named {name!r}") from None

    async def close(self) -> None:
        """Tear down every managed analyser and port (LIFO)."""
        await self._close(suppress_errors=False)

    async def _close(self, *, suppress_errors: bool) -> None:
        async with self._state_lock:
            if self._closed:
                return
            errors: list[BaseException] = []
            for name in reversed(list(self._devices.keys())):
                entry = self._devices.pop(name)
                try:
                    await self._teardown_device(entry)
                except Exception as err:
                    _logger.warning("manager.close_device_failed device=%s error=%r", name, err)
                    errors.append(err)
            self._closed = True
            if errors and not suppress_errors:
                raise BaseExceptionGroup("manager.close: teardown failures", errors)

    # ----------------------------------------------------------- concurrent I/O

    async def poll_samples(
        self, *, names: Sequence[str] | None = None, timeout: float | None = None
    ) -> list[Sample]:
        """Poll every (or named) analyser concurrently across ports → flat samples.

        One :class:`Sample` per channel read. Failed devices are dropped and logged
        at WARN (the recorder never sees them). Same-port devices serialise on the
        shared port lock, acquired once per port-group so a coherent snapshot is
        not interleaved. Satisfies :class:`PollSource`.
        """
        groups = self._group_by_port(self._resolve_names(names))
        result_lock = anyio.Lock()
        all_samples: list[Sample] = []

        async def _run_group(port_key: str, members: list[str]) -> None:
            local: list[Sample] = []
            lock = self._lock_for(port_key)
            async with lock:
                for member in members:
                    try:
                        local.extend(
                            await self._devices[member].analyzer.poll_samples(timeout=timeout)
                        )
                    except ServomexError as err:
                        _logger.warning("manager.poll_failed device=%s error=%r", member, err)
            async with result_lock:
                all_samples.extend(local)

        async with anyio.create_task_group() as tg:
            for port_key, members in groups.items():
                tg.start_soon(_run_group, port_key, members)
        return all_samples

    async def poll(
        self, names: Sequence[str] | None = None, *, timeout: float | None = None
    ) -> Mapping[str, DeviceResult[Frame]]:
        """Read one :class:`Frame` per (or named) analyser, keyed by name.

        Cross-port concurrent, same-port serialised. Always returns a complete
        mapping; per-device failures land in :attr:`DeviceResult.error`.
        """
        groups = self._group_by_port(self._resolve_names(names))
        results: dict[str, DeviceResult[Frame]] = {}
        result_lock = anyio.Lock()

        async def _run_group(port_key: str, members: list[str]) -> None:
            lock = self._lock_for(port_key)
            async with lock:
                for member in members:
                    try:
                        frame = await self._devices[member].analyzer.poll(timeout=timeout)
                    except ServomexError as err:
                        async with result_lock:
                            results[member] = DeviceResult.failure(err)
                    else:
                        async with result_lock:
                            results[member] = DeviceResult[Frame].success(frame)

        async with anyio.create_task_group() as tg:
            for port_key, members in groups.items():
                tg.start_soon(_run_group, port_key, members)
        return results

    async def execute_each[T](
        self,
        op: Callable[[Analyzer], Awaitable[T]],
        names: Sequence[str] | None = None,
    ) -> dict[str, DeviceResult[T]]:
        """Run ``op(analyzer)`` on every (or named) analyser concurrently across ports."""
        groups = self._group_by_port(self._resolve_names(names))
        results: dict[str, DeviceResult[T]] = {}
        errors: list[ServomexError] = []
        result_lock = anyio.Lock()

        async def _run_group(port_key: str, members: list[str]) -> None:
            lock = self._lock_for(port_key)
            async with lock:
                for member in members:
                    try:
                        value = await op(self._devices[member].analyzer)
                    except ServomexError as err:
                        async with result_lock:
                            results[member] = DeviceResult.failure(err)
                            errors.append(err)
                    else:
                        async with result_lock:
                            results[member] = DeviceResult[T].success(value)

        async with anyio.create_task_group() as tg:
            for port_key, members in groups.items():
                tg.start_soon(_run_group, port_key, members)

        if self._error_policy is ErrorPolicy.RAISE and errors:
            raise ExceptionGroup("manager.execute_each: one or more analysers failed", errors)
        return results

    # ----------------------------------------------------------------- internals

    def _check_open(self) -> None:
        if self._closed:
            raise ServomexConnectionError("manager is closed")

    def _lock_for(self, port_key: str) -> anyio.Lock:
        entry = self._ports.get(port_key)
        return entry.lock if entry is not None else anyio.Lock()

    def _resolve_names(self, names: Sequence[str] | None) -> tuple[str, ...]:
        if names is None:
            return tuple(self._devices.keys())
        targets = tuple(names)
        unknown = [n for n in targets if n not in self._devices]
        if unknown:
            raise ServomexValidationError(f"manager: unknown analyser name(s) {sorted(unknown)!r}")
        return targets

    def _group_by_port(self, names: Sequence[str]) -> dict[str, list[str]]:
        """Group target names by canonical port key for concurrent dispatch."""
        groups: dict[str, list[str]] = {}
        for n in names:
            entry = self._devices[n]
            key = entry.port_key if entry.port_key is not None else f"solo:{n}"
            groups.setdefault(key, []).append(n)
        return groups

    async def _resolve_port(
        self,
        source: str | Transport,
        *,
        protocol: ProtocolKind,
        serial_settings: SerialSettings | None,
    ) -> tuple[str, _PortEntry]:
        """Get (or build) the shared :class:`_PortEntry` for ``source``."""
        from servomexlib.devices.factory import resolve_transport  # noqa: PLC0415

        if isinstance(source, str):
            port_key = _canonical_port_key(source)
        else:
            port_key = f"transport:{id(source)}"

        port_entry = self._ports.get(port_key)
        if port_entry is None:
            transport = await resolve_transport(source, serial_settings)
            port_entry = _PortEntry(
                key=port_key,
                transport=transport,
                lock=anyio.Lock(),
                owns_transport=isinstance(source, str),
                protocol=protocol,
            )
            self._ports[port_key] = port_entry
        elif port_entry.protocol is not None and port_entry.protocol is not protocol:
            raise ServomexValidationError(
                "manager.add: cannot mix protocols on one port "
                f"({port_entry.protocol.value} vs {protocol.value})",
                context=ErrorContext(protocol=protocol, port=port_entry.transport.label),
            )
        return port_key, port_entry

    def _build_analyzer(
        self,
        port_entry: _PortEntry,
        *,
        name: str,
        protocol: ProtocolKind,
        address: int,
        timeout: float,
    ) -> Analyzer:
        """Build an :class:`Analyzer` against the port's shared transport."""
        from servomexlib.devices.factory import build_client  # noqa: PLC0415

        client = build_client(port_entry.transport, protocol, address=address, timeout=timeout)
        return Analyzer(client, device=name)

    async def _teardown_device(self, entry: _DeviceEntry) -> None:
        """Release a device's port ref, closing the shared transport on last ref."""
        if entry.port_key is None:
            return  # caller-owned pre-built Analyzer
        port_entry = self._ports.get(entry.port_key)
        if port_entry is None:
            return
        port_entry.refs.discard(entry.name)
        if not port_entry.refs:
            self._ports.pop(entry.port_key, None)
            if port_entry.owns_transport:
                try:
                    await port_entry.transport.aclose()
                except Exception as err:
                    _logger.warning(
                        "manager.close_port_failed port=%s error=%r", entry.port_key, err
                    )
