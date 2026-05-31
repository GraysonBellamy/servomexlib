"""Sync manager / sinks / recording wrappers.

Plain (synchronous) tests — the facade runs without an event loop in the caller's
thread. The Modbus cases share one :class:`SyncPortal` with the in-process mock so
the fake's anyio streams and the wrapper's calls live on one loop.
"""

from __future__ import annotations

import csv as csv_module
from typing import TYPE_CHECKING

from servomexlib.protocol.base import ProtocolKind
from servomexlib.sync import SyncManager, record_to_sink, sync_csv_sink
from servomexlib.sync.portal import SyncPortal
from servomexlib.testing import mock_modbus_transport

if TYPE_CHECKING:
    from pathlib import Path


def test_sync_manager_poll_samples() -> None:
    portal = SyncPortal()
    portal.__enter__()
    mock_cm = portal.wrap_async_context_manager(mock_modbus_transport())
    transport, _slave = mock_cm.__enter__()
    mgr = SyncManager(portal=portal)
    mgr.__enter__()
    try:
        mgr.add("a1", transport, protocol=ProtocolKind.MODBUS_RTU, address=1)
        samples = mgr.poll_samples()
        assert {s.device for s in samples} == {"a1"}
        assert mgr.names == ("a1",)
    finally:
        mgr.__exit__(None, None, None)
        mock_cm.__exit__(None, None, None)
        portal.__exit__(None, None, None)


def test_sync_record_to_sink_csv(tmp_path: Path) -> None:
    out = tmp_path / "run.csv"
    portal = SyncPortal()
    portal.__enter__()
    mock_cm = portal.wrap_async_context_manager(mock_modbus_transport())
    transport, _slave = mock_cm.__enter__()
    mgr = SyncManager(portal=portal)
    mgr.__enter__()
    try:
        analyzer = mgr.add("a1", transport, protocol=ProtocolKind.MODBUS_RTU, address=1)
        summary = record_to_sink(
            analyzer,
            sync_csv_sink(out, portal=portal)._sink,
            rate_hz=50,
            duration=0.1,
            portal=portal,
        )
        assert summary.samples_emitted > 0
    finally:
        mgr.__exit__(None, None, None)
        mock_cm.__exit__(None, None, None)
        portal.__exit__(None, None, None)
    with out.open(newline="") as fh:
        rows = list(csv_module.DictReader(fh))
    assert rows
    assert "channel" in rows[0]
