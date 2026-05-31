"""Live autocalibration round-trip against the bench 4100D (opt-in).

Marked ``hardware_stateful`` and gated on ``SERVOMEXLIB_ENABLE_STATEFUL_TESTS=1``
(see ``conftest.py``); never runs in CI. These drive the *real* control surface —
``start_calibration`` injects cal gas on the analyser — so they are deliberately
hard to trigger by accident.

Run (from an env with ``anyserial``/``anymodbus`` and the unit on ``COM11``)::

    SERVOMEXLIB_ENABLE_STATEFUL_TESTS=1 \\
    SERVOMEXLIB_HARDWARE_PORT=COM11 SERVOMEXLIB_HARDWARE_ADDRESS=1 \\
        uv run pytest tests/hardware -m hardware_stateful

Override the port/address via the ``SERVOMEXLIB_HARDWARE_PORT`` /
``SERVOMEXLIB_HARDWARE_ADDRESS`` env vars (default ``COM11`` / ``1``).
"""

from __future__ import annotations

import os

import pytest

from servomexlib import open_device
from servomexlib.devices.models import CalPhase
from servomexlib.protocol.base import ProtocolKind

pytestmark = [pytest.mark.anyio, pytest.mark.hardware_stateful]

_PORT = os.environ.get("SERVOMEXLIB_HARDWARE_PORT", "COM11")
_ADDRESS = int(os.environ.get("SERVOMEXLIB_HARDWARE_ADDRESS", "1"))


@pytest.fixture
def anyio_backend() -> str:
    """Pin live hardware tests to a single backend (avoid re-opening the port)."""
    return "asyncio"


async def test_autocal_status_is_readable() -> None:
    """Reading cal status is non-destructive; it must return a valid phase."""
    async with await open_device(_PORT, protocol=ProtocolKind.MODBUS_RTU, address=_ADDRESS) as anz:
        prog = await anz.calibration_status(1)
        assert prog.group == 1
        assert isinstance(prog.phase, CalPhase)


async def test_autocal_start_then_stop_round_trip() -> None:
    """Start cal-group 1, observe it goes active, then stop and observe idle."""
    async with await open_device(_PORT, protocol=ProtocolKind.MODBUS_RTU, address=_ADDRESS) as anz:
        try:
            await anz.start_calibration(1, confirm=True)
            active = await anz.calibration_status(1)
            assert active.active
        finally:
            await anz.stop_calibration(confirm=True)
        settled = await anz.calibration_status(1)
        assert not settled.active
        assert settled.phase is CalPhase.IDLE
