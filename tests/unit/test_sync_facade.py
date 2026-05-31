"""The sync facade mirrors the async API.

These are intentionally plain (synchronous) functions: the whole point of the
facade is that it runs without an event loop in the caller's thread. The Modbus
cases share one :class:`SyncPortal` with the in-process mock so the fake's anyio
streams and the analyzer's calls live on the same event loop.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest

from servomexlib.errors import (
    ServomexConfirmationRequiredError,
    ServomexProtocolUnsupportedError,
)
from servomexlib.protocol.base import ProtocolKind
from servomexlib.registry.channels import ChannelId
from servomexlib.sync import Servomex, SyncAnalyzer, SyncPortal
from servomexlib.testing import FakeTransport, mock_modbus_transport
from tests.conftest import approx


@contextmanager
def _sync_modbus_analyzer(*, identify: bool):
    portal = SyncPortal()
    portal.__enter__()
    # Wrap the mock CM so its task-group enter/exit share one portal task.
    mock_cm = portal.wrap_async_context_manager(mock_modbus_transport())
    transport, _slave = mock_cm.__enter__()
    anz = SyncAnalyzer.open(
        transport, protocol=ProtocolKind.MODBUS_RTU, identify=identify, portal=portal
    )
    try:
        yield anz
    finally:
        # Cancel the mock serve task (and close its streams) BEFORE the analyzer
        # closes the shared transport, so serve never observes a mid-read EOF.
        mock_cm.__exit__(None, None, None)
        anz.__exit__(None, None, None)
        portal.__exit__(None, None, None)


def test_sync_modbus_poll_matches_async() -> None:
    with _sync_modbus_analyzer(identify=True) as anz:
        assert anz.protocol is ProtocolKind.MODBUS_RTU
        i1 = anz.read_channel(ChannelId.I1)
        assert i1.name == "Oxygen"
        assert i1.value == approx(20.378, abs=1e-3)
        assert anz.info is not None
        frame = anz.poll()
        assert frame.channel(ChannelId.I1).value == approx(20.378, abs=1e-3)


def test_sync_calibration_gate_then_pulse() -> None:
    with _sync_modbus_analyzer(identify=False) as anz:
        with pytest.raises(ServomexConfirmationRequiredError):
            anz.start_calibration(1)
        anz.start_calibration(1, confirm=True)  # confirm passes → coil pulse runs
        assert anz.calibration_status(1).group == 1


def test_sync_continuous_open_and_protocol_string() -> None:
    fake = FakeTransport()
    with Servomex.open(fake, protocol="continuous", identify=False) as anz:
        assert isinstance(anz, SyncAnalyzer)
        assert anz.protocol is ProtocolKind.CONTINUOUS_ASCII


def test_sync_continuous_calibration_unsupported() -> None:
    fake = FakeTransport()
    with Servomex.open(fake, protocol="continuous", identify=False) as anz:
        with pytest.raises(ServomexProtocolUnsupportedError):
            anz.start_calibration(1, confirm=True)
