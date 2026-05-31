"""Unit coercion, pint mapping, and the percent↔vpm trace scale."""

from __future__ import annotations

import pytest

from servomexlib.registry.units import (
    VPM_PER_PERCENT,
    Unit,
    UnitKind,
    coerce_unit,
    kind_of,
    percent_to_vpm,
    to_pint,
    vpm_to_percent,
)
from tests.conftest import approx


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (" % ", Unit.PERCENT),
        ("%", Unit.PERCENT),
        (" mA", Unit.MILLIAMP),
        ("MA", Unit.MILLIAMP),
        ("vpm", Unit.VPM),
        ("ppm", Unit.PPM),
        ("  ", Unit.UNKNOWN),
        ("wat", Unit.UNKNOWN),
    ],
)
def test_coerce_unit(text: str, expected: Unit) -> None:
    assert coerce_unit(text) is expected


def test_kind_of() -> None:
    assert kind_of(Unit.PERCENT) is UnitKind.CONCENTRATION
    assert kind_of(Unit.MILLIAMP) is UnitKind.CURRENT
    assert kind_of(Unit.UNKNOWN) is UnitKind.UNKNOWN


def test_to_pint() -> None:
    assert to_pint(Unit.PERCENT) == "percent"
    assert to_pint(Unit.MILLIAMP) == "milliampere"
    assert to_pint(Unit.UNKNOWN) is None


def test_vpm_scale() -> None:
    assert VPM_PER_PERCENT == 10_000
    assert percent_to_vpm(1.0) == approx(10_000.0)
    assert vpm_to_percent(10_000.0) == approx(1.0)
