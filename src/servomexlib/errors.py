"""Typed exception hierarchy for :mod:`servomexlib`.

Every library exception inherits from :class:`ServomexError` and carries a
structured :class:`ErrorContext`. The ``message`` is the
human-readable summary; the context is the machine-readable detail (port,
protocol, address, channel, register/function code, request/response bytes,
elapsed time).

The pattern matches the ``*lib`` family: ``ErrorContext`` is a frozen
``dataclass(slots=True)`` whose ``extra`` mapping is always frozen into a
read-only :class:`types.MappingProxyType`, and :meth:`ServomexError.with_context`
does a slot-safe copy so an inner layer can raise and an outer layer enrich.

**MRO caution.** Cross-branch classes are kept single-rooted: every
exception resolves :meth:`__init__` and :attr:`with_context` through exactly one
path. Where a class genuinely belongs to two branches (a sink dependency that is
also a configuration problem) the two bases share the single
``ServomexError.__init__`` and define no competing ``__slots__``, so the MRO is
unambiguous — see the construction/``with_context`` round-trip test.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, replace
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Self

if TYPE_CHECKING:
    from collections.abc import Mapping

    from servomexlib.protocol.base import ProtocolKind
    from servomexlib.registry.channels import ChannelId


_EMPTY_EXTRA: Mapping[str, Any] = MappingProxyType({})


def _empty_extra() -> Mapping[str, Any]:
    return _EMPTY_EXTRA


@dataclass(frozen=True, slots=True)
class ErrorContext:
    """Structured context attached to every :class:`ServomexError`.

    Fields are best-effort — missing data is ``None`` rather than raising.

    ``extra`` accepts any ``Mapping`` and is always frozen into a read-only
    :class:`types.MappingProxyType` at construction so the shared empty
    sentinel can never be mutated through ``error.context.extra[k] = v``.
    """

    port: str | None = None
    protocol: ProtocolKind | str | None = None
    address: int | None = None
    channel: ChannelId | str | None = None
    register: int | None = None
    function_code: int | None = None
    request: bytes | None = None
    response: bytes | None = None
    elapsed_s: float | None = None
    extra: Mapping[str, Any] = field(default_factory=_empty_extra)

    def __post_init__(self) -> None:
        if not isinstance(self.extra, MappingProxyType):
            object.__setattr__(self, "extra", MappingProxyType(dict(self.extra)))

    def merged(self, **updates: Any) -> Self:
        """Return a new context with ``updates`` overlaid. Unknown keys go to ``extra``."""
        known: dict[str, Any] = {}
        extra_updates: dict[str, Any] = {}
        for key, value in updates.items():
            if key in _CONTEXT_KNOWN_FIELDS:
                known[key] = value
            else:
                extra_updates[key] = value

        new_extra: Mapping[str, Any] = (
            MappingProxyType({**self.extra, **extra_updates}) if extra_updates else self.extra
        )
        return replace(self, **known, extra=new_extra)


_CONTEXT_KNOWN_FIELDS: frozenset[str] = frozenset(
    f.name for f in fields(ErrorContext) if f.name != "extra"
)


_EMPTY_CONTEXT = ErrorContext()


class ServomexError(Exception):
    """Base class for every :mod:`servomexlib` exception.

    Carries a typed :class:`ErrorContext`. The ``message`` is the human-readable
    summary; the context is the machine-readable detail.
    """

    context: ErrorContext

    def __init__(self, message: str = "", *, context: ErrorContext | None = None) -> None:
        super().__init__(message)
        self.context = context if context is not None else _EMPTY_CONTEXT

    def with_context(self, **updates: Any) -> Self:
        """Return a copy of this error with its context updated.

        Useful when an inner layer raises and an outer layer wants to enrich
        the context (for instance adding ``port`` or ``elapsed_s``).
        """
        cls = type(self)
        new = cls.__new__(cls)
        new.args = self.args
        try:
            new.__dict__.update(self.__dict__)
        except AttributeError:  # pragma: no cover — no slotted subclass today
            for slot in getattr(cls, "__slots__", ()):
                if hasattr(self, slot):
                    object.__setattr__(new, slot, getattr(self, slot))
        new.context = self.context.merged(**updates)
        new.__cause__ = self.__cause__
        new.__context__ = self.__context__
        new.__traceback__ = self.__traceback__
        return new

    def __str__(self) -> str:
        base = super().__str__()
        ctx = self.context
        bits: list[str] = []
        if ctx.protocol is not None:
            protocol = ctx.protocol
            bits.append(f"protocol={getattr(protocol, 'value', protocol)}")
        if ctx.port is not None:
            bits.append(f"port={ctx.port}")
        if ctx.address is not None:
            bits.append(f"address={ctx.address}")
        if ctx.channel is not None:
            channel = ctx.channel
            bits.append(f"channel={getattr(channel, 'value', channel)}")
        if ctx.register is not None:
            bits.append(f"register={ctx.register}")
        if ctx.function_code is not None:
            bits.append(f"fc=0x{ctx.function_code:02X}")
        if ctx.elapsed_s is not None:
            bits.append(f"elapsed_s={ctx.elapsed_s:.3f}")
        if ctx.request is not None:
            bits.append(f"request={ctx.request!r}")
        if ctx.response is not None:
            bits.append(f"response={ctx.response!r}")
        if ctx.extra:
            bits.append(f"extra={dict(ctx.extra)!r}")
        return f"{base} [{', '.join(bits)}]" if bits else base


# --- Configuration -------------------------------------------------------


class ServomexConfigurationError(ServomexError):
    """Configuration-level error (bad args, wrong confirm flag, etc.)."""


class ServomexValidationError(ServomexConfigurationError):
    """Request validation failed before I/O (bad channel, group out of range)."""


class ServomexConfirmationRequiredError(ServomexConfigurationError):
    """A ``SafetyTier.STATEFUL`` op was attempted without ``confirm=True``."""


# --- Transport -----------------------------------------------------------


class ServomexTransportError(ServomexError):
    """I/O-layer error from the serial transport."""


class ServomexTimeoutError(ServomexTransportError):
    """A transport read or write timed out (or a Modbus request got no reply)."""


class ServomexConnectionError(ServomexTransportError):
    """Could not open / lost the connection (or no recognised protocol on AUTO)."""


# --- Protocol ------------------------------------------------------------


class ServomexProtocolError(ServomexError):
    """Protocol-level error (framing, parsing, checksum, mode mismatch)."""


class ServomexParseError(ServomexProtocolError):
    """A frame could not be parsed (bad header, unparsable field)."""


class ServomexChecksumError(ServomexProtocolError):
    """A continuous-frame checksum did not match the recomputed value."""


class ServomexFrameError(ServomexProtocolError):
    """Structural frame error (wrong field count, truncated block)."""


class ServomexProtocolUnsupportedError(ServomexProtocolError):
    """The active protocol cannot perform this operation (e.g. autocal in continuous)."""


# --- Modbus --------------------------------------------------------------


class ServomexModbusError(ServomexProtocolError):
    """A Modbus exception response or engine-level Modbus failure.

    Rooted under :class:`ServomexProtocolError` (single MRO path) so the
    inherited ``__init__`` and :meth:`~ServomexError.with_context` resolve
    unambiguously; the per-exception-code subclasses below add no competing
    ``__init__`` / ``__slots__``.
    """


class ServomexIllegalFunctionError(ServomexModbusError):
    """Modbus exception code 01 — function not supported by the slave."""


class ServomexIllegalDataAddressError(ServomexModbusError):
    """Modbus exception code 02 — data address not valid for the slave."""


# --- Capability ----------------------------------------------------------


class ServomexCapabilityError(ServomexError):
    """An operation is not available on this device / mode."""


class ServomexUnsupportedCommandError(ServomexCapabilityError):
    """The device does not support the requested command."""


# --- Sinks ---------------------------------------------------------------


class ServomexSinkError(ServomexError):
    """Base class for errors raised by sinks (CSV, JSONL, SQLite, Parquet, Postgres)."""


class ServomexSinkDependencyError(ServomexSinkError, ServomexConfigurationError):
    """A sink's optional backing library is not installed.

    Multi-inherits :class:`ServomexConfigurationError` because a missing extra is
    a configuration problem from the caller's perspective. Both bases share the
    single :class:`ServomexError` ``__init__`` and define no ``__slots__``, so the
    MRO stays single-pathed (see the MRO caution above).
    """


class ServomexSinkSchemaError(ServomexSinkError):
    """A batch's shape is incompatible with the sink's locked schema."""


class ServomexSinkWriteError(ServomexSinkError):
    """The backing store rejected a write (driver exception wrapped via ``from``)."""


__all__ = [
    "ErrorContext",
    "ServomexCapabilityError",
    "ServomexChecksumError",
    "ServomexConfigurationError",
    "ServomexConfirmationRequiredError",
    "ServomexConnectionError",
    "ServomexError",
    "ServomexFrameError",
    "ServomexIllegalDataAddressError",
    "ServomexIllegalFunctionError",
    "ServomexModbusError",
    "ServomexParseError",
    "ServomexProtocolError",
    "ServomexProtocolUnsupportedError",
    "ServomexSinkDependencyError",
    "ServomexSinkError",
    "ServomexSinkSchemaError",
    "ServomexSinkWriteError",
    "ServomexTimeoutError",
    "ServomexTransportError",
    "ServomexUnsupportedCommandError",
    "ServomexValidationError",
]
