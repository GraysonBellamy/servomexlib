"""PostgreSQL sink — :mod:`asyncpg`, COPY by default, parameterised fallback.

:class:`PostgresSink` writes one row per :class:`Sample` into a PostgreSQL table.
``asyncpg`` is optional (``servomexlib[postgres]``); the import is deferred to
:meth:`open`, so instantiation works on bare-core installs and
:class:`~servomexlib.errors.ServomexSinkDependencyError` is raised only when the
user actually opens a connection.

Defaults: **binary COPY** (≈5-10× faster than INSERT; disable via
``use_copy=False`` where the role lacks COPY privilege), a connection **pool**,
strict **identifier validation** on ``schema``/``table`` with ``$N`` placeholders,
**credential scrubbing** in logs (via :meth:`PostgresConfig.target`), and a server
``statement_timeout``. Schema locks on first batch when ``create_table=True``,
else from ``information_schema.columns`` on open.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Self
from urllib.parse import urlparse

from servomexlib._logging import get_logger
from servomexlib.errors import (
    ServomexSinkDependencyError,
    ServomexSinkSchemaError,
    ServomexSinkWriteError,
)
from servomexlib.sinks._schema import ColumnSpec, SchemaLock
from servomexlib.sinks.base import sample_to_row

if TYPE_CHECKING:
    from collections.abc import Sequence
    from types import TracebackType

    from servomexlib.streaming.sample import Sample

__all__ = ["PostgresConfig", "PostgresSink"]

_logger = get_logger("sinks.postgres")

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")

_PG_NUMERIC_FLOAT = frozenset({"double precision", "real", "numeric", "decimal"})
_PG_NUMERIC_INT = frozenset({"bigint", "integer", "smallint"})


def _validate_identifier(name: str, *, label: str) -> str:
    """Return ``name`` if it is a safe SQL identifier; raise otherwise."""
    if not _IDENTIFIER_PATTERN.fullmatch(name):
        msg = (
            f"{label} must match [A-Za-z_][A-Za-z0-9_]{{0,62}}; got {name!r}. "
            "Schema/table names are interpolated into CREATE/INSERT statements."
        )
        raise ValueError(msg)
    return name


def _column_type(spec: ColumnSpec) -> str:
    """Map a :class:`ColumnSpec` to a PostgreSQL type literal."""
    if spec.python_type is float:
        return "double precision"
    if spec.python_type is int:
        return "bigint"
    return "text"


@dataclass(frozen=True, slots=True)
class PostgresConfig:
    """Connection + target settings for :class:`PostgresSink`.

    Either ``dsn`` or the discrete ``host``/``user``/``database`` set must be
    provided (mutually exclusive). Credentials are never logged.
    """

    dsn: str | None = field(default=None, repr=False)  # may embed credentials
    host: str | None = None
    port: int = 5432
    user: str | None = None
    password: str | None = field(default=None, repr=False)
    database: str | None = None
    schema: str = "public"
    table: str = "samples"
    pool_min_size: int = 1
    pool_max_size: int = 4
    statement_timeout_ms: int = 30_000
    command_timeout_s: float = 10.0
    create_table: bool = False
    use_copy: bool = True

    def __post_init__(self) -> None:
        if self.dsn is None and self.host is None:
            raise ValueError(
                "PostgresConfig requires either `dsn` or `host` (and related discrete "
                "fields); both were None."
            )
        if self.dsn is not None and self.host is not None:
            raise ValueError(
                "PostgresConfig: `dsn` and `host` are mutually exclusive — pick one style."
            )
        _validate_identifier(self.schema, label="schema name")
        _validate_identifier(self.table, label="table name")
        if self.pool_min_size < 1 or self.pool_max_size < self.pool_min_size:
            raise ValueError(
                f"PostgresConfig: pool bounds invalid "
                f"(min={self.pool_min_size}, max={self.pool_max_size})."
            )
        if self.statement_timeout_ms < 0:
            raise ValueError(
                f"statement_timeout_ms must be >= 0, got {self.statement_timeout_ms!r}"
            )
        if self.command_timeout_s <= 0:
            raise ValueError(f"command_timeout_s must be > 0, got {self.command_timeout_s!r}")

    def target(self) -> str:
        """Return a log-safe ``host:port/db.schema.table`` (no password)."""
        if self.dsn is not None:
            parsed = urlparse(self.dsn)
            host = parsed.hostname or "?"
            port = parsed.port or self.port
            db = (parsed.path or "/?").lstrip("/") or "?"
        else:
            host = self.host or "?"
            port = self.port
            db = self.database or "?"
        return f"{host}:{port}/{db}.{self.schema}.{self.table}"


def _load_asyncpg() -> Any:
    """Lazy-import asyncpg; raise :class:`ServomexSinkDependencyError` on miss."""
    try:
        import asyncpg  # pyright: ignore[reportMissingImports, reportMissingTypeStubs]  # noqa: PLC0415
    except ImportError as exc:
        raise ServomexSinkDependencyError(
            "PostgresSink requires the `postgres` extra. "
            "Install with: `pip install 'servomexlib[postgres]'` "
            "(or `uv add 'servomexlib[postgres]'`)."
        ) from exc
    return asyncpg


class PostgresSink:
    """Append-only Postgres writer using pooled asyncpg connections.

    Attributes:
        config: Frozen :class:`PostgresConfig` instance.
        columns: Locked columns in order, or ``None`` before first flush.
    """

    def __init__(self, config: PostgresConfig) -> None:
        self._config = config
        self._schema = SchemaLock(sink_name="postgres", logger=_logger)
        self._asyncpg: Any = None
        self._pool: Any = None
        self._insert_sql: str | None = None
        self._rows_written = 0

    @property
    def config(self) -> PostgresConfig:
        """The frozen :class:`PostgresConfig` passed in at construction."""
        return self._config

    @property
    def columns(self) -> tuple[ColumnSpec, ...] | None:
        """Locked columns in order, or ``None`` before first :meth:`write_many`."""
        return self._schema.columns

    async def open(self) -> None:
        """Load asyncpg, open the pool, and (optionally) introspect the table."""
        if self._pool is not None:
            return
        self._asyncpg = _load_asyncpg()
        cfg = self._config
        server_settings = {
            "application_name": "servomexlib",
            "statement_timeout": str(int(cfg.statement_timeout_ms)),
        }
        try:
            if cfg.dsn is not None:
                self._pool = await self._asyncpg.create_pool(
                    dsn=cfg.dsn,
                    min_size=cfg.pool_min_size,
                    max_size=cfg.pool_max_size,
                    command_timeout=cfg.command_timeout_s,
                    server_settings=server_settings,
                )
            else:
                self._pool = await self._asyncpg.create_pool(
                    host=cfg.host,
                    port=cfg.port,
                    user=cfg.user,
                    password=cfg.password,
                    database=cfg.database,
                    min_size=cfg.pool_min_size,
                    max_size=cfg.pool_max_size,
                    command_timeout=cfg.command_timeout_s,
                    server_settings=server_settings,
                )
        except Exception as exc:
            raise ServomexSinkWriteError(
                f"PostgresSink: failed to open pool for {cfg.target()}: {exc}"
            ) from exc

        _logger.info(
            "sinks.postgres.open target=%s pool_min=%s pool_max=%s use_copy=%s create_table=%s",
            cfg.target(),
            cfg.pool_min_size,
            cfg.pool_max_size,
            cfg.use_copy,
            cfg.create_table,
        )

        if not cfg.create_table:
            await self._introspect_existing_table()

    async def _introspect_existing_table(self) -> None:
        """Read ``information_schema.columns`` and lock the schema."""
        cfg = self._config
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT column_name, data_type
                    FROM information_schema.columns
                    WHERE table_schema = $1 AND table_name = $2
                    ORDER BY ordinal_position
                    """,
                    cfg.schema,
                    cfg.table,
                )
        except Exception as exc:
            raise ServomexSinkWriteError(
                f"PostgresSink: failed to introspect "
                f"{cfg.schema}.{cfg.table} on {cfg.target()}: {exc}"
            ) from exc
        if not rows:
            raise ServomexSinkSchemaError(
                f"PostgresSink: table {cfg.schema}.{cfg.table} does not exist on "
                f"{cfg.target()} and create_table=False. Create the table first, or pass "
                "create_table=True on PostgresConfig."
            )
        specs: list[ColumnSpec] = []
        for row in rows:
            data_type = str(row["data_type"]).lower()
            if data_type in _PG_NUMERIC_FLOAT:
                py_type: type = float
            elif data_type in _PG_NUMERIC_INT:
                py_type = int
            else:
                py_type = str
            specs.append(
                ColumnSpec(name=str(row["column_name"]), python_type=py_type, nullable=True)
            )
        self._schema.lock_to(specs)
        self._insert_sql = self._build_insert_sql()

    async def write_many(self, samples: Sequence[Sample]) -> None:
        """Append ``samples`` — one COPY (or executemany) per call."""
        if self._pool is None:
            raise RuntimeError("PostgresSink: write_many called before open()")
        if not samples:
            return

        rows = [sample_to_row(s) for s in samples]

        if not self._schema.is_locked:
            assert self._config.create_table  # noqa: S101
            self._schema.lock(rows)
            await self._create_table()
            self._insert_sql = self._build_insert_sql()

        columns = self._schema.columns
        assert columns is not None  # noqa: S101
        assert self._insert_sql is not None  # noqa: S101

        projected_tuples = [
            tuple(self._schema.project(row)[spec.name] for spec in columns) for row in rows
        ]

        try:
            if self._config.use_copy:
                await self._write_copy(projected_tuples, columns)
            else:
                await self._write_executemany(projected_tuples)
        except ServomexSinkWriteError:
            raise
        except Exception as exc:
            raise ServomexSinkWriteError(
                f"PostgresSink: write failed for {self._config.target()}: {exc}"
            ) from exc
        self._rows_written += len(projected_tuples)

    async def _write_copy(
        self, records: Sequence[tuple[object, ...]], columns: Sequence[ColumnSpec]
    ) -> None:
        """Bulk-insert ``records`` using asyncpg's binary COPY path."""
        cfg = self._config
        async with self._pool.acquire() as conn:
            await conn.copy_records_to_table(
                cfg.table,
                records=list(records),
                columns=[spec.name for spec in columns],
                schema_name=cfg.schema,
                timeout=cfg.command_timeout_s,
            )

    async def _write_executemany(self, records: Sequence[tuple[object, ...]]) -> None:
        """Insert ``records`` via prepared ``executemany`` (COPY-off fallback)."""
        assert self._insert_sql is not None  # noqa: S101
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.executemany(self._insert_sql, records)

    def _build_insert_sql(self) -> str:
        """Compose the parameterised INSERT used by the executemany fallback."""
        columns = self._schema.columns
        assert columns is not None  # noqa: S101
        col_list = ", ".join(f'"{spec.name}"' for spec in columns)
        placeholders = ", ".join(f"${i + 1}" for i in range(len(columns)))
        cfg = self._config
        return (
            f'INSERT INTO "{cfg.schema}"."{cfg.table}" '  # noqa: S608
            f"({col_list}) VALUES ({placeholders})"
        )

    async def _create_table(self) -> None:
        """Issue ``CREATE TABLE IF NOT EXISTS`` from the inferred schema."""
        cfg = self._config
        columns = self._schema.columns
        assert columns is not None  # noqa: S101
        col_defs = ", ".join(f'"{spec.name}" {_column_type(spec)}' for spec in columns)
        stmt = f'CREATE TABLE IF NOT EXISTS "{cfg.schema}"."{cfg.table}" ({col_defs})'
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(stmt)
        except Exception as exc:
            raise ServomexSinkWriteError(
                f"PostgresSink: CREATE TABLE failed for {cfg.schema}.{cfg.table}: {exc}"
            ) from exc

    async def close(self) -> None:
        """Close the pool. Idempotent."""
        if self._pool is None:
            return
        pool = self._pool
        self._pool = None
        try:
            await pool.close()
        finally:
            _logger.info(
                "sinks.postgres.close target=%s rows_written=%s",
                self._config.target(),
                self._rows_written,
            )
        self._asyncpg = None

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
