"""Sink round-trips + the pipe() driver."""

from __future__ import annotations

import csv as csv_module
import json
import sqlite3
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from servomexlib.devices.models import Reading
from servomexlib.protocol.base import ProtocolKind
from servomexlib.registry.channels import ChannelId, ChannelKind
from servomexlib.registry.status import decode_discrete_status
from servomexlib.registry.units import Unit
from servomexlib.sinks import CsvSink, InMemorySink, JsonlSink, SqliteSink, pipe, sample_to_row
from servomexlib.streaming import Sample
from tests.conftest import approx

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

pytestmark = pytest.mark.anyio


def _sample(value: float = 20.38, *, error: bool = False) -> Sample:
    if error:
        return Sample(
            device="dev",
            channel=None,
            reading=None,
            protocol=ProtocolKind.CONTINUOUS_ASCII,
            monotonic_ns=time.monotonic_ns(),
            received_at=datetime.now(UTC),
            error=ValueError("bad frame"),
        )
    reading = Reading(
        channel=ChannelId.I1,
        kind=ChannelKind.TRANSDUCER,
        name="Oxygen",
        value=value,
        unit=Unit.PERCENT,
        status=decode_discrete_status((False,) * 8, ChannelKind.TRANSDUCER),
        protocol=ProtocolKind.MODBUS_RTU,
        received_at=datetime.now(UTC),
        monotonic_ns=time.monotonic_ns(),
        raw=b"",
    )
    return Sample(
        device="dev",
        channel=ChannelId.I1,
        reading=reading,
        protocol=ProtocolKind.MODBUS_RTU,
        monotonic_ns=reading.monotonic_ns,
        received_at=reading.received_at,
    )


def test_sample_to_row_reading_and_error() -> None:
    row = sample_to_row(_sample())
    assert row["channel"] == "I1"
    assert row["name"] == "Oxygen"
    assert row["value"] == approx(20.38)
    assert row["unit"] == "%"
    assert row["ok"] == "true"
    assert row["error"] is None

    err = sample_to_row(_sample(error=True))
    assert err["channel"] is None
    assert err["value"] is None
    assert err["error"] == "bad frame"


async def test_memory_sink_collects() -> None:
    async with InMemorySink() as sink:
        await sink.write_many([_sample(1.0), _sample(2.0)])
    assert [s.reading.value for s in sink.samples if s.reading] == [1.0, 2.0]


async def test_csv_sink_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "out.csv"
    async with CsvSink(path) as sink:
        await sink.write_many([_sample(20.38), _sample(0.08)])
    with path.open(newline="") as fh:
        rows = list(csv_module.DictReader(fh))
    assert len(rows) == 2
    assert rows[0]["channel"] == "I1"
    assert rows[0]["unit"] == "%"


async def test_jsonl_sink_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "out.jsonl"
    async with JsonlSink(path) as sink:
        await sink.write_many([_sample(20.38)])
        await sink.write_many([_sample(0.08)])
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert [r["value"] for r in rows] == [approx(20.38), approx(0.08)]


async def test_sqlite_sink_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "out.db"
    async with SqliteSink(path, table="samples") as sink:
        await sink.write_many([_sample(20.38), _sample(0.08)])
    conn = sqlite3.connect(str(path))
    try:
        count = conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
        first = conn.execute("SELECT channel, unit FROM samples LIMIT 1").fetchone()
    finally:
        conn.close()
    assert count == 2
    assert first == ("I1", "%")


async def test_pipe_drains_batches_into_sink() -> None:
    async def _batches() -> AsyncIterator[list[Sample]]:
        for _ in range(3):
            yield [_sample(1.0), _sample(2.0)]

    sink = InMemorySink()
    async with sink:
        summary = await pipe(_batches(), sink, batch_size=2, flush_interval=0.01)
    assert summary.samples_emitted == 6
    assert len(sink.samples) == 6
