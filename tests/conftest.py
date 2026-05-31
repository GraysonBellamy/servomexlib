"""Shared pytest configuration.

AnyIO's own pytest plugin drives async tests. The ``anyio_backend`` fixture
parametrizes tests across asyncio, asyncio+uvloop when uvloop is available,
and trio. Matches the backend matrix used by the sibling ``alicatlib`` and
``anyserial`` packages and catches regressions that only surface under one
scheduler.
"""

from __future__ import annotations

import sys
from importlib.util import find_spec
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from _pytest.mark.structures import ParameterSet
    from _pytest.python_api import ApproxBase


def approx(
    expected: object,
    *,
    rel: float | None = None,
    abs: float | None = None,  # noqa: A002 - mirrors pytest.approx's parameter name
    nan_ok: bool = False,
) -> ApproxBase:
    """Typed shim over :func:`pytest.approx`.

    ``pytest.approx`` has untyped parameters, so every call site trips pyright's
    strict ``reportUnknownMemberType``. Funnelling through this typed wrapper
    confines that to a single suppressed line here.
    """
    return pytest.approx(expected, rel=rel, abs=abs, nan_ok=nan_ok)  # pyright: ignore[reportUnknownMemberType]


#: The live 6-frame continuous capture off COM11 — the parser/checksum baseline.
CONTINUOUS_FIXTURE = (
    Path(__file__).parent / "fixtures" / "captures" / "continuous_4100_idle_5ch.bin"
)


@pytest.fixture
def continuous_capture() -> bytes:
    """Raw bytes of the 6-frame continuous capture."""
    return CONTINUOUS_FIXTURE.read_bytes()


@pytest.fixture
def continuous_frames(continuous_capture: bytes) -> list[bytes]:
    """The capture split into individual frame payloads (no trailing CRLF)."""
    return [frame for frame in continuous_capture.split(b"\r\n") if frame.strip(b" ")]


_UVLOOP_UNAVAILABLE = sys.platform == "win32" or find_spec("uvloop") is None

_PARAMS: list[ParameterSet] = [
    pytest.param(("asyncio", {"use_uvloop": False}), id="asyncio"),
    pytest.param(
        ("asyncio", {"use_uvloop": True}),
        id="asyncio+uvloop",
        marks=pytest.mark.skipif(
            _UVLOOP_UNAVAILABLE,
            reason="uvloop is unsupported or not installed on this platform",
        ),
    ),
    pytest.param("trio", id="trio"),
]


@pytest.fixture(params=_PARAMS)
def anyio_backend(request: pytest.FixtureRequest) -> object:
    """Run async tests against asyncio, asyncio+uvloop when available, and trio."""
    return request.param
