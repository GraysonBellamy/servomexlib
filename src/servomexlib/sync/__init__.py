"""Synchronous facade for :mod:`servomexlib`.

Wraps the async core behind an ``anyio`` blocking portal so scripts, notebooks,
and the REPL can use the library without ``async``/``await``::

    from servomexlib.sync import Servomex

    with Servomex.open("COM11", protocol="continuous") as anz:
        print(anz.poll())

:class:`SyncManager` mirrors :class:`~servomexlib.manager.ServomexManager`,
:class:`SyncSink` mirrors the sinks, and :func:`record_to_sink` runs a blocking
acquisition into a sink.
"""

from __future__ import annotations

from servomexlib.sync.analyzer import Servomex, SyncAnalyzer, SyncStreamingSession
from servomexlib.sync.manager import SyncManager
from servomexlib.sync.portal import SyncPortal
from servomexlib.sync.recording import record_to_sink
from servomexlib.sync.sinks import (
    SyncSink,
    sync_csv_sink,
    sync_jsonl_sink,
    sync_sqlite_sink,
)

__all__ = [
    "Servomex",
    "SyncAnalyzer",
    "SyncManager",
    "SyncPortal",
    "SyncSink",
    "SyncStreamingSession",
    "record_to_sink",
    "sync_csv_sink",
    "sync_jsonl_sink",
    "sync_sqlite_sink",
]
