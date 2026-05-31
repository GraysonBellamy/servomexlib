"""anymodbus exception → ServomexError mapping."""

from __future__ import annotations

import anymodbus
import pytest

from servomexlib.errors import (
    ErrorContext,
    ServomexConnectionError,
    ServomexIllegalDataAddressError,
    ServomexIllegalFunctionError,
    ServomexModbusError,
    ServomexTimeoutError,
)
from servomexlib.protocol.base import ProtocolKind
from servomexlib.protocol.modbus.errors import remap_modbus_exception


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (anymodbus.IllegalFunctionError(function_code=0x04), ServomexIllegalFunctionError),
        (anymodbus.ModbusUnsupportedFunctionError(0x41), ServomexIllegalFunctionError),
        (anymodbus.IllegalDataAddressError(function_code=0x04), ServomexIllegalDataAddressError),
        (anymodbus.CRCError("bad crc"), ServomexTimeoutError),
        (anymodbus.FrameTimeoutError("silence"), ServomexTimeoutError),
        (anymodbus.BusClosedError("closed"), ServomexConnectionError),
        (anymodbus.ConnectionLostError("lost"), ServomexConnectionError),
    ],
)
def test_known_exceptions_map(exc: Exception, expected: type) -> None:
    assert isinstance(remap_modbus_exception(exc), expected)


def test_lrc_error_maps_to_timeout() -> None:
    assert isinstance(remap_modbus_exception(anymodbus.LRCError("bad lrc")), ServomexTimeoutError)


def test_unknown_modbus_error_falls_back() -> None:
    mapped = remap_modbus_exception(anymodbus.ModbusError("weird"))
    assert isinstance(mapped, ServomexModbusError)


def test_context_is_attached() -> None:
    mapped = remap_modbus_exception(
        anymodbus.IllegalDataAddressError(function_code=0x04),
        context=ErrorContext(protocol=ProtocolKind.MODBUS_RTU, address=30, function_code=0x04),
    )
    assert mapped.context.address == 30
    assert mapped.context.function_code == 0x04
