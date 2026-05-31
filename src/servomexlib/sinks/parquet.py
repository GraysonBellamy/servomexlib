"""Parquet sink — :mod:`pyarrow`, schema locked, zstd by default.

:class:`ParquetSink` writes one row per :class:`Sample` into a single Parquet
file. pyarrow is optional (``servomexlib[parquet]``); the import is deferred to
:meth:`open`, so instantiating the sink succeeds on bare-core installs and
:class:`~servomexlib.errors.ServomexSinkDependencyError` is raised only when the
user actually opens the file.

Defaults: **zstd** compression, dictionary encoding on, one row group per
:meth:`write_many`. Schema is locked on the first batch
(:class:`~servomexlib.sinks._schema.SchemaLock`); unknown columns dropped with a
one-shot WARN. Files are unreadable until the footer flushes on :meth:`close` —
rely on the recorder's structured exit so ``__aexit__`` always runs.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Self

from servomexlib._logging import get_logger
from servomexlib.errors import ServomexSinkDependencyError, ServomexSinkWriteError
from servomexlib.sinks._schema import ColumnSpec, SchemaLock
from servomexlib.sinks.base import sample_to_row

if TYPE_CHECKING:
    from collections.abc import Sequence
    from types import TracebackType

    from servomexlib.streaming.sample import Sample

__all__ = ["ParquetSink"]

_logger = get_logger("sinks.parquet")

_Compression = Literal["zstd", "snappy", "gzip", "brotli", "lz4", "none"]


def _load_pyarrow() -> tuple[Any, Any]:
    """Lazy-import pyarrow; raise :class:`ServomexSinkDependencyError` on miss."""
    try:
        import pyarrow as pa_module  # pyright: ignore[reportMissingImports, reportMissingTypeStubs]  # noqa: PLC0415
        import pyarrow.parquet as pq_module  # pyright: ignore[reportMissingImports, reportMissingTypeStubs]  # noqa: PLC0415
    except ImportError as exc:
        raise ServomexSinkDependencyError(
            "ParquetSink requires the `parquet` extra. "
            "Install with: `pip install 'servomexlib[parquet]'` "
            "(or `uv add 'servomexlib[parquet]'`)."
        ) from exc
    return pa_module, pq_module


class ParquetSink:
    """Append-only Parquet writer with first-batch schema lock.

    Attributes:
        path: Destination Parquet file.
        compression: Codec applied to every row group.
        columns: Locked columns in order, or ``None`` before first flush.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        compression: _Compression = "zstd",
        use_dictionary: bool = True,
        row_group_size: int | None = None,
    ) -> None:
        self._path = Path(path)
        self._compression: _Compression = compression
        self._use_dictionary = use_dictionary
        if row_group_size is not None and row_group_size < 1:
            raise ValueError(f"row_group_size must be >= 1 if set, got {row_group_size!r}")
        self._row_group_size = row_group_size
        self._schema = SchemaLock(sink_name="parquet", logger=_logger)
        self._pa: Any = None
        self._pq: Any = None
        self._arrow_schema: Any = None
        self._writer: Any = None
        self._rows_written = 0

    @property
    def path(self) -> Path:
        """Destination Parquet file path."""
        return self._path

    @property
    def compression(self) -> _Compression:
        """The configured compression codec."""
        return self._compression

    @property
    def columns(self) -> tuple[ColumnSpec, ...] | None:
        """Locked columns in order, or ``None`` before first :meth:`write_many`."""
        return self._schema.columns

    async def open(self) -> None:
        """Load pyarrow and create the parent directory. Idempotent.

        The ``ParquetWriter`` itself opens lazily on the first
        :meth:`write_many`, when the concrete schema is known.
        """
        if self._pa is not None:
            return
        self._pa, self._pq = _load_pyarrow()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        _logger.info(
            "sinks.parquet.open path=%s compression=%s", str(self._path), self._compression
        )

    async def write_many(self, samples: Sequence[Sample]) -> None:
        """Append ``samples`` as one Parquet row group."""
        if self._pa is None:
            raise RuntimeError("ParquetSink: write_many called before open()")
        if not samples:
            return

        rows = [sample_to_row(s) for s in samples]

        if not self._schema.is_locked:
            self._schema.lock(rows)
            self._arrow_schema = self._build_arrow_schema()
            self._writer = self._open_writer()

        assert self._writer is not None  # noqa: S101 — narrow for type checker
        assert self._arrow_schema is not None  # noqa: S101

        projected = [self._schema.project(r) for r in rows]
        columns = self._schema.columns
        assert columns is not None  # noqa: S101

        arrays = {spec.name: [row[spec.name] for row in projected] for spec in columns}
        try:
            table = self._pa.Table.from_pydict(arrays, schema=self._arrow_schema)
            self._writer.write_table(table, row_group_size=self._row_group_size)
        except Exception as exc:
            raise ServomexSinkWriteError(
                f"ParquetSink: write failed for {self._path}: {exc}"
            ) from exc
        self._rows_written += len(projected)

    def _build_arrow_schema(self) -> Any:
        """Map locked :class:`ColumnSpec` list to a :class:`pyarrow.Schema`."""
        assert self._pa is not None  # noqa: S101
        columns = self._schema.columns
        assert columns is not None  # noqa: S101
        pa_module = self._pa
        fields: list[Any] = []
        for spec in columns:
            if spec.python_type is float:
                arrow_type = pa_module.float64()
            elif spec.python_type is int:
                arrow_type = pa_module.int64()
            else:
                arrow_type = pa_module.string()
            fields.append(pa_module.field(spec.name, arrow_type, nullable=True))
        return pa_module.schema(fields)

    def _open_writer(self) -> Any:
        """Create the pyarrow ParquetWriter with our configured codec."""
        assert self._pq is not None  # noqa: S101
        assert self._arrow_schema is not None  # noqa: S101
        return self._pq.ParquetWriter(
            str(self._path),
            self._arrow_schema,
            compression=self._compression,
            use_dictionary=self._use_dictionary,
        )

    async def close(self) -> None:
        """Flush the footer and close the writer. Idempotent."""
        if self._writer is not None:
            try:
                self._writer.close()
            finally:
                self._writer = None
        self._pa = None
        self._pq = None
        _logger.info(
            "sinks.parquet.close path=%s rows_written=%s", str(self._path), self._rows_written
        )

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
