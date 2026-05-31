"""Shared first-batch schema-lock for tabular sinks.

Every tabular sink (SQLite, Parquet, Postgres, CSV) shares one schema-evolution
policy:

1. **First batch wins.** The column set and order are locked from the first
   :meth:`write_many`; for schema-ful sinks that locked spec drives the backing
   schema.
2. **Unknown columns are dropped with a one-shot WARN.** A later batch carrying a
   new key (e.g. a hot-plugged channel) does not reshape the file/table silently.
3. **Missing columns are filled with ``None``.** Row projection guarantees a
   stable shape per row.

Sink-facing only; no public re-export.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import logging
    from collections.abc import Mapping, Sequence

__all__ = ["ColumnSpec", "SchemaLock"]

_SCALAR_TYPE = type[float] | type[int] | type[str] | type[bool]


@dataclass(frozen=True, slots=True)
class ColumnSpec:
    """One column in a locked tabular schema.

    Attributes:
        name: Column name, verbatim from the source row dict.
        python_type: Concrete Python scalar type — :class:`float`, :class:`int`,
            or :class:`str`. Sinks translate this into their native type system.
        nullable: ``True`` if the first batch held a ``None`` for this column, or
            the column was absent from some rows.
    """

    name: str
    python_type: _SCALAR_TYPE
    nullable: bool


class SchemaLock:
    """Lock a row-dict schema on first batch; drop unknowns on later batches.

    Not thread-safe. Each sink owns one :class:`SchemaLock` and guards it with
    whatever lock protects its write path.
    """

    def __init__(self, *, sink_name: str, logger: logging.Logger) -> None:
        self._sink_name = sink_name
        self._logger = logger
        self._columns: tuple[ColumnSpec, ...] | None = None
        self._names: frozenset[str] = frozenset()
        self._unknown_warned: set[str] = set()

    @property
    def columns(self) -> tuple[ColumnSpec, ...] | None:
        """The locked columns in declaration order, or ``None`` before lock."""
        return self._columns

    @property
    def is_locked(self) -> bool:
        """``True`` once :meth:`lock` or :meth:`lock_to` has been called."""
        return self._columns is not None

    def lock(self, rows: Sequence[Mapping[str, object]]) -> tuple[ColumnSpec, ...]:
        """Infer column specs from ``rows`` and lock the schema.

        Column order is first-encounter across the batch. Per-column type is the
        first non-``None`` value's type; mixed int/float widen to float; any other
        mix widens to str. All-``None`` columns default to ``str`` / nullable.

        Raises:
            RuntimeError: ``lock`` already called.
            ValueError: ``rows`` is empty.
        """
        if self._columns is not None:
            raise RuntimeError("SchemaLock.lock called twice")
        if not rows:
            raise ValueError("SchemaLock.lock requires a non-empty first batch")

        ordered_keys: list[str] = []
        seen: set[str] = set()
        for row in rows:
            for key in row:
                if key not in seen:
                    ordered_keys.append(key)
                    seen.add(key)

        specs = [self._infer_column(key, rows) for key in ordered_keys]
        self._columns = tuple(specs)
        self._names = frozenset(ordered_keys)
        return self._columns

    @staticmethod
    def _infer_column(key: str, rows: Sequence[Mapping[str, object]]) -> ColumnSpec:
        """Infer one column's spec from the first batch."""
        inferred: type | None = None
        nullable = False
        for row in rows:
            if key not in row:
                nullable = True
                continue
            value = row[key]
            if value is None:
                nullable = True
                continue
            value_type = type(value)
            if inferred is None:
                inferred = value_type
            elif inferred is not value_type:
                inferred = float if {inferred, value_type} <= {int, float} else str
        if inferred is None:
            inferred = str
            nullable = True
        elif inferred not in (float, int, str):
            inferred = str
        return ColumnSpec(name=key, python_type=inferred, nullable=nullable)

    def lock_to(self, specs: Sequence[ColumnSpec]) -> tuple[ColumnSpec, ...]:
        """Lock the schema from an externally-supplied spec list.

        Used by sinks that validate against an existing backing schema (e.g.
        Postgres with ``create_table=False``) rather than inferring.

        Raises:
            RuntimeError: ``lock_to`` already called.
            ValueError: ``specs`` is empty.
        """
        if self._columns is not None:
            raise RuntimeError("SchemaLock.lock_to called twice")
        if not specs:
            raise ValueError("SchemaLock.lock_to requires at least one column")
        self._columns = tuple(specs)
        self._names = frozenset(spec.name for spec in self._columns)
        return self._columns

    def project(self, row: Mapping[str, object]) -> dict[str, object]:
        """Return a new dict with only locked-schema keys (missing → ``None``).

        Unknown keys are dropped; the first occurrence of each logs at WARN.

        Raises:
            RuntimeError: schema not locked yet.
        """
        if self._columns is None:
            raise RuntimeError("SchemaLock.project called before lock()")

        result: dict[str, object] = {spec.name: None for spec in self._columns}
        for key, value in row.items():
            if key in self._names:
                result[key] = value
                continue
            if key not in self._unknown_warned:
                self._unknown_warned.add(key)
                self._logger.warning(
                    "sink.unknown_column_dropped sink=%s column=%s", self._sink_name, key
                )
        return result
