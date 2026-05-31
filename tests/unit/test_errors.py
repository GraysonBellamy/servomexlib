"""Error hierarchy: construction, context round-trips, single-MRO cross-branch."""

from __future__ import annotations

import pytest

from servomexlib.errors import (
    ErrorContext,
    ServomexConfigurationError,
    ServomexError,
    ServomexIllegalFunctionError,
    ServomexModbusError,
    ServomexProtocolError,
    ServomexSinkDependencyError,
    ServomexSinkError,
)
from servomexlib.protocol.base import ProtocolKind


def test_base_construction_carries_empty_context() -> None:
    err = ServomexError("boom")
    assert str(err).startswith("boom")
    assert err.context == ErrorContext()


def test_context_renders_into_str() -> None:
    err = ServomexError(
        "boom",
        context=ErrorContext(port="COM11", protocol=ProtocolKind.MODBUS_RTU, address=30),
    )
    rendered = str(err)
    assert "port=COM11" in rendered
    assert "protocol=modbus_rtu" in rendered
    assert "address=30" in rendered


def test_with_context_returns_enriched_copy_same_type() -> None:
    original = ServomexIllegalFunctionError("nope", context=ErrorContext(address=30))
    enriched = original.with_context(port="COM11", function_code=0x04)

    assert type(enriched) is ServomexIllegalFunctionError
    assert enriched is not original
    # original is untouched
    assert original.context.port is None
    # enriched merges old + new
    assert enriched.context.address == 30
    assert enriched.context.port == "COM11"
    assert enriched.context.function_code == 0x04


def test_merged_routes_unknown_keys_to_extra() -> None:
    ctx = ErrorContext(port="COM11").merged(port="COM3", retries=2)
    assert ctx.port == "COM3"
    assert ctx.extra["retries"] == 2


def test_extra_is_frozen() -> None:
    ctx = ErrorContext(extra={"a": 1})
    with pytest.raises(TypeError):
        ctx.extra["b"] = 2  # type: ignore[index]


def test_modbus_errors_are_single_rooted() -> None:
    # ServomexIllegalFunctionError resolves __init__/with_context on one path:
    # exactly one base class chain up to ServomexError (no diamond).
    assert issubclass(ServomexIllegalFunctionError, ServomexModbusError)
    assert issubclass(ServomexModbusError, ServomexProtocolError)
    assert ServomexError in ServomexIllegalFunctionError.__mro__
    # The MRO has no competing __init__: with_context round-trips cleanly.
    err = ServomexIllegalFunctionError("x").with_context(address=1)
    assert err.context.address == 1


def test_sink_dependency_is_both_sink_and_configuration_error() -> None:
    err = ServomexSinkDependencyError("install servomexlib[parquet]")
    assert isinstance(err, ServomexSinkError)
    assert isinstance(err, ServomexConfigurationError)
    # Shared single ServomexError.__init__ — with_context still works through the diamond.
    assert err.with_context(port="COM11").context.port == "COM11"
