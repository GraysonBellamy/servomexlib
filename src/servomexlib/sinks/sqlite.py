"""SQLite sink — stdlib :mod:`sqlite3` + WAL, parameterised ``executemany``.

:class:`SqliteSink` writes one row per :class:`Sample` into a local SQLite file.
Core (no extra) because ``sqlite3`` ships with the stdlib. The driver is
synchronous; the sink calls it through :func:`anyio.to_thread.run_sync` so the
event loop stays responsive.

Defaults: ``journal_mode=WAL`` + ``synchronous=NORMAL``, ``busy_timeout=5000``,
one ``BEGIN IMMEDIATE``…``COMMIT`` per ``write_many`` (one fsync per batch). The
table name is validated against a strict identifier regex; values always pass
through ``?`` parameters. Schema is locked on the first batch
(:class:`~servomexlib.sinks._schema.SchemaLock`); unknowns dropped with a WARN.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Self

from anyio.to_thread import run_sync

from servomexlib._logging import get_logger
from servomexlib.errors import ServomexSinkSchemaError, ServomexSinkWriteError
from servomexlib.sinks._schema import ColumnSpec, SchemaLock
from servomexlib.sinks.base import sample_to_row

if TYPE_CHECKING:
    from collections.abc import Sequence
    from types import TracebackType

    from servomexlib.streaming.sample import Sample

__all__ = ["SqliteSink"]

_logger = get_logger("sinks.sqlite")

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")

_JournalMode = Literal["WAL", "DELETE", "MEMORY", "TRUNCATE", "PERSIST", "OFF"]
_Synchronous = Literal["FULL", "NORMAL", "OFF", "EXTRA"]


def _validate_identifier(name: str, *, label: str) -> str:
    """Return ``name`` if it is a safe SQL identifier; raise otherwise."""
    if not _IDENTIFIER_PATTERN.fullmatch(name):
        msg = (
            f"{label} must match [A-Za-z_][A-Za-z0-9_]{{0,62}}; got {name!r}. "
            "Table names are interpolated into CREATE/INSERT statements."
        )
        raise ValueError(msg)
    return name


def _column_type(spec: ColumnSpec) -> str:
    """Map a :class:`ColumnSpec` to a SQLite type affinity."""
    if spec.python_type is float:
        return "REAL"
    if spec.python_type is int:
        return "INTEGER"
    return "TEXT"


class SqliteSink:
    """Append-only SQLite writer with WAL journaling and first-batch schema lock.

    Attributes:
        path: Destination SQLite file, created on :meth:`open`.
        table: Target table name (validated).
        columns: The locked :class:`ColumnSpec` tuple, or ``None`` before flush.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        table: str = "samples",
        create_table: bool = True,
        journal_mode: _JournalMode = "WAL",
        synchronous: _Synchronous = "NORMAL",
        busy_timeout_ms: int = 5000,
    ) -> None:
        self._path = Path(path)
        self._table = _validate_identifier(table, label="table name")
        self._create_table = create_table
        self._journal_mode: _JournalMode = journal_mode
        self._synchronous: _Synchronous = synchronous
        if busy_timeout_ms < 0:
            raise ValueError(f"busy_timeout_ms must be >= 0, got {busy_timeout_ms!r}")
        self._busy_timeout_ms = busy_timeout_ms
        self._conn: sqlite3.Connection | None = None
        self._schema = SchemaLock(sink_name="sqlite", logger=_logger)
        self._insert_sql: str | None = None

    @property
    def path(self) -> Path:
        """Destination SQLite file path."""
        return self._path

    @property
    def table(self) -> str:
        """Target table name (validated)."""
        return self._table

    @property
    def columns(self) -> tuple[ColumnSpec, ...] | None:
        """Locked columns in order, or ``None`` before first :meth:`write_many`."""
        return self._schema.columns

    async def open(self) -> None:
        """Open the connection, apply PRAGMAs, and introspect the target. Idempotent."""
        if self._conn is not None:
            return
        self._conn = await run_sync(self._connect_blocking)
        _logger.info(
            "sinks.sqlite.open path=%s table=%s journal_mode=%s synchronous=%s",
            str(self._path),
            self._table,
            self._journal_mode,
            self._synchronous,
        )
        if not self._create_table:
            try:
                await run_sync(self._introspect_existing_table_blocking)
            except BaseException:
                conn = self._conn
                self._conn = None
                await run_sync(conn.close)
                raise

    def _connect_blocking(self) -> sqlite3.Connection:
        """Open the connection and apply PRAGMAs. Runs off-loop."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._path), isolation_level=None, check_same_thread=False)
        conn.execute(f"PRAGMA journal_mode = {self._journal_mode}")
        conn.execute(f"PRAGMA synchronous = {self._synchronous}")
        conn.execute(f"PRAGMA busy_timeout = {int(self._busy_timeout_ms)}")
        return conn

    def _introspect_existing_table_blocking(self) -> None:
        """Read ``PRAGMA table_info`` and lock the schema to those columns."""
        assert self._conn is not None  # noqa: S101 — narrow for type checker
        cursor = self._conn.execute(f'PRAGMA table_info("{self._table}")')
        rows = cursor.fetchall()
        if not rows:
            msg = (
                f"SqliteSink: table {self._table!r} does not exist in {self._path} "
                "and create_table=False. Create the table first or pass create_table=True."
            )
            raise ServomexSinkSchemaError(msg)
        specs: list[ColumnSpec] = []
        for _cid, name, decl_type, notnull, _default, _pk in rows:
            upper = (decl_type or "").upper()
            if "INT" in upper:
                py_type: type = int
            elif any(token in upper for token in ("REAL", "FLOA", "DOUB")):
                py_type = float
            else:
                py_type = str
            specs.append(ColumnSpec(name=name, python_type=py_type, nullable=not notnull))
        self._schema.lock_to(specs)
        self._insert_sql = self._build_insert_sql()

    async def write_many(self, samples: Sequence[Sample]) -> None:
        """Append ``samples`` as rows in a single transaction (parameterised)."""
        if self._conn is None:
            raise RuntimeError("SqliteSink: write_many called before open()")
        if not samples:
            return

        rows = [sample_to_row(s) for s in samples]

        if not self._schema.is_locked and self._create_table:
            self._schema.lock(rows)
            await run_sync(self._create_table_blocking)
            self._insert_sql = self._build_insert_sql()

        assert self._insert_sql is not None  # noqa: S101 — narrow for type checker
        columns = self._schema.columns
        assert columns is not None  # noqa: S101

        projected = [
            tuple(self._schema.project(row)[spec.name] for spec in columns) for row in rows
        ]
        await run_sync(self._executemany_blocking, projected)

    def _build_insert_sql(self) -> str:
        """Compose the parameterised INSERT for the locked column set."""
        columns = self._schema.columns
        assert columns is not None  # noqa: S101 — narrow for type checker
        col_list = ", ".join(f'"{spec.name}"' for spec in columns)
        placeholders = ", ".join("?" for _ in columns)
        return f'INSERT INTO "{self._table}" ({col_list}) VALUES ({placeholders})'  # noqa: S608

    def _create_table_blocking(self) -> None:
        """Issue ``CREATE TABLE IF NOT EXISTS`` from the locked schema."""
        assert self._conn is not None  # noqa: S101
        columns = self._schema.columns
        assert columns is not None  # noqa: S101
        col_defs = ", ".join(f'"{spec.name}" {_column_type(spec)}' for spec in columns)
        stmt = f'CREATE TABLE IF NOT EXISTS "{self._table}" ({col_defs})'
        try:
            self._conn.execute(stmt)
        except sqlite3.Error as exc:
            raise ServomexSinkWriteError(
                f"SqliteSink: CREATE TABLE failed for {self._table!r}: {exc}"
            ) from exc

    def _executemany_blocking(self, rows: Sequence[tuple[object, ...]]) -> None:
        """Run the batch insert inside one transaction."""
        assert self._conn is not None  # noqa: S101
        assert self._insert_sql is not None  # noqa: S101
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            self._conn.executemany(self._insert_sql, rows)
            self._conn.execute("COMMIT")
        except sqlite3.Error as exc:
            try:
                self._conn.execute("ROLLBACK")
            except sqlite3.Error:
                _logger.exception("sinks.sqlite.rollback_failed")
            raise ServomexSinkWriteError(
                f"SqliteSink: INSERT into {self._table!r} failed: {exc}"
            ) from exc

    async def close(self) -> None:
        """Close the connection. Idempotent."""
        if self._conn is None:
            return
        conn = self._conn
        self._conn = None
        try:
            await run_sync(conn.close)
        finally:
            _logger.info("sinks.sqlite.close path=%s table=%s", str(self._path), self._table)

    async def __aenter__(self) -> Self:
        await self.open()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        del exc_type, exc, tb
        await self.close()
