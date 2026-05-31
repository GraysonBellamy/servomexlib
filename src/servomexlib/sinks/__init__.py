"""Sinks — durable destinations for streamed :class:`Sample` rows.

The :class:`SampleSink` Protocol plus :func:`pipe` (the recorder→sink driver) and
:func:`sample_to_row` (the long-format flattener). Core sinks are stdlib-only
(memory/csv/jsonl/sqlite); :class:`ParquetSink` (``[parquet]``) and
:class:`PostgresSink` (``[postgres]``) lazy-import their optional deps in
:meth:`open`, raising :class:`~servomexlib.errors.ServomexSinkDependencyError` if
the extra is missing — so importing this package never requires the extras.
"""

from __future__ import annotations

from servomexlib.sinks.base import SampleSink, pipe, sample_to_row
from servomexlib.sinks.csv import CsvSink
from servomexlib.sinks.jsonl import JsonlSink
from servomexlib.sinks.memory import InMemorySink
from servomexlib.sinks.parquet import ParquetSink
from servomexlib.sinks.postgres import PostgresConfig, PostgresSink
from servomexlib.sinks.sqlite import SqliteSink

__all__ = [
    "CsvSink",
    "InMemorySink",
    "JsonlSink",
    "ParquetSink",
    "PostgresConfig",
    "PostgresSink",
    "SampleSink",
    "SqliteSink",
    "pipe",
    "sample_to_row",
]
