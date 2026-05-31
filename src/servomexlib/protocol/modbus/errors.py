"""Map ``anymodbus`` exceptions onto the :class:`ServomexError` family.

The Modbus engine raises its own typed exceptions; this module translates them at
the boundary so callers only ever see ``servomexlib`` errors. Exception code 01 →
:class:`ServomexIllegalFunctionError`, code 02 → :class:`ServomexIllegalDataAddressError`;
a framing/CRC/LRC error or no-response → :class:`ServomexTimeoutError`; a closed/lost
bus → :class:`ServomexConnectionError`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import anymodbus

from servomexlib.errors import (
    ServomexConfigurationError,
    ServomexConnectionError,
    ServomexError,
    ServomexIllegalDataAddressError,
    ServomexIllegalFunctionError,
    ServomexModbusError,
    ServomexProtocolError,
    ServomexTimeoutError,
)

if TYPE_CHECKING:
    from servomexlib.errors import ErrorContext


def remap_modbus_exception(exc: Exception, *, context: ErrorContext | None = None) -> ServomexError:
    """Translate an ``anymodbus`` exception into the matching ``ServomexError``.

    Non-Modbus exceptions are wrapped in a generic :class:`ServomexModbusError`
    so nothing leaks the upstream type. The returned error chains ``exc`` via
    ``__cause__`` at the raise site (``raise remap(...) from exc``).
    """
    message = str(exc) or type(exc).__name__
    error = _classify(exc, message)
    if context is not None:
        return error.with_context(**_context_kwargs(context))
    return error


# Ordered most-specific → least-specific; the first matching row wins. A
# framing/CRC/LRC error or silence means no usable response → ServomexTimeoutError.
_RULES: tuple[tuple[tuple[type[Exception], ...], type[ServomexError]], ...] = (
    (
        (anymodbus.IllegalFunctionError, anymodbus.ModbusUnsupportedFunctionError),
        ServomexIllegalFunctionError,
    ),
    ((anymodbus.IllegalDataAddressError,), ServomexIllegalDataAddressError),
    (
        (
            anymodbus.CRCError,
            anymodbus.LRCError,
            anymodbus.ChecksumError,
            anymodbus.FrameError,
            anymodbus.FrameTimeoutError,
        ),
        ServomexTimeoutError,
    ),
    ((anymodbus.BusClosedError, anymodbus.ConnectionLostError), ServomexConnectionError),
    ((anymodbus.ConfigurationError,), ServomexConfigurationError),
    ((anymodbus.UnexpectedResponseError,), ServomexProtocolError),
)


def _classify(exc: Exception, message: str) -> ServomexError:
    for types, error_cls in _RULES:
        if isinstance(exc, types):
            return error_cls(message)
    # Any other anymodbus error (or a non-Modbus exception) → generic Modbus error.
    return ServomexModbusError(message)


def _context_kwargs(context: ErrorContext) -> dict[str, object]:
    kwargs: dict[str, object] = {}
    for name in (
        "port",
        "protocol",
        "address",
        "channel",
        "register",
        "function_code",
        "request",
        "response",
        "elapsed_s",
    ):
        value = getattr(context, name)
        if value is not None:
            kwargs[name] = value
    return kwargs


__all__ = ["remap_modbus_exception"]
