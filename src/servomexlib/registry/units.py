"""Units for the 4000-series.

:class:`Unit` is a local :class:`enum.StrEnum` (no ``pint`` dependency);
:func:`to_pint` returns pint-compatible strings for callers who want them.
The prime measurement is **percent**; trace ``vpm`` carries a default
×10 000 scale (1 % = 10 000 vpm), modelled here.
"""

from __future__ import annotations

from enum import StrEnum

#: Volume parts-per-million per percent — the 4000-series trace scale.
VPM_PER_PERCENT = 10_000


class Unit(StrEnum):
    """A measurement unit. The value is the canonical display string."""

    PERCENT = "%"
    VPM = "vpm"
    PPM = "ppm"
    MILLIAMP = "mA"
    UNKNOWN = "?"


class UnitKind(StrEnum):
    """Coarse classification of a :class:`Unit`."""

    CONCENTRATION = "concentration"
    CURRENT = "current"
    UNKNOWN = "unknown"


_BY_TEXT: dict[str, Unit] = {
    "%": Unit.PERCENT,
    "vpm": Unit.VPM,
    "ppm": Unit.PPM,
    "ma": Unit.MILLIAMP,
}

_KIND: dict[Unit, UnitKind] = {
    Unit.PERCENT: UnitKind.CONCENTRATION,
    Unit.VPM: UnitKind.CONCENTRATION,
    Unit.PPM: UnitKind.CONCENTRATION,
    Unit.MILLIAMP: UnitKind.CURRENT,
    Unit.UNKNOWN: UnitKind.UNKNOWN,
}

_PINT: dict[Unit, str | None] = {
    Unit.PERCENT: "percent",
    Unit.VPM: "ppm",
    Unit.PPM: "ppm",
    Unit.MILLIAMP: "milliampere",
    Unit.UNKNOWN: None,
}


def coerce_unit(text: str) -> Unit:
    """Map a (possibly padded, mixed-case) unit string to a :class:`Unit`.

    Unknown unit text maps to :attr:`Unit.UNKNOWN` rather than raising, so a
    single unrecognised unit never sinks a whole frame parse.
    """
    return _BY_TEXT.get(text.strip().casefold(), Unit.UNKNOWN)


def kind_of(unit: Unit) -> UnitKind:
    """Return the :class:`UnitKind` for ``unit``."""
    return _KIND.get(unit, UnitKind.UNKNOWN)


def to_pint(unit: Unit) -> str | None:
    """Return a ``pint``-compatible unit string, or ``None`` if not mappable."""
    return _PINT.get(unit)


def percent_to_vpm(percent: float) -> float:
    """Convert a percent concentration to volume parts-per-million."""
    return percent * VPM_PER_PERCENT


def vpm_to_percent(vpm: float) -> float:
    """Convert a volume parts-per-million concentration to percent."""
    return vpm / VPM_PER_PERCENT


__all__ = [
    "VPM_PER_PERCENT",
    "Unit",
    "UnitKind",
    "coerce_unit",
    "kind_of",
    "percent_to_vpm",
    "to_pint",
    "vpm_to_percent",
]
