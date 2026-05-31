"""Safety-gated autocalibration — coil-pulse semantics + gate re-assertion.

The control surface itself is
exercised here against the byte-accurate ``MockSlave`` fake: the 0→1→0 coil pulse
must be *ordered* (write-on → FC01 readback → write-off), ``calibration_status``
must reflect a live (non-idle) cal-group bank, and every gate must fire **before
any byte is sent** (proven via the recorded coil-op log being empty).

The ``SAMPLING`` :class:`CalPhase` is reachable only from the continuous-frame
autocal field; the Modbus cal-group discrete decode models a 2-bit (calibrating +
gas-1/2) pair per group, so it yields ``IDLE`` / ``CAL_GAS_1`` / ``CAL_GAS_2``
(distinguishing a separate sample phase on the wire is a HW-pending item,
not asserted here).
"""

from __future__ import annotations

import pytest

from servomexlib import open_device
from servomexlib.devices.models import CalPhase
from servomexlib.errors import (
    ServomexConfirmationRequiredError,
    ServomexProtocolUnsupportedError,
    ServomexValidationError,
)
from servomexlib.protocol.base import ProtocolKind
from servomexlib.testing import (
    CoilOp,
    FakeTransport,
    coil_ops,
    load_cal_state,
    mock_modbus_pair,
    mock_modbus_transport,
)

pytestmark = pytest.mark.anyio

# FC05 = write single coil, FC01 = read coils. Cal-group start coils lower to
# PDU 0..3 (data-model 1..4); the stop-all coil (data-model 9) lowers to PDU 8.
_FC_WRITE_COIL = 0x05
_FC_READ_COILS = 0x01


# --- coil-pulse ordering --------------------------------------------------


async def test_start_calibration_pulses_coil_0_to_1_to_0() -> None:
    """start_calibration(1) writes coil PDU 0 on, reads it back, then writes off."""
    async with mock_modbus_pair(record_coil_ops=True) as (client, slave):
        await client.start_calibration(1)
        assert coil_ops(slave) == [
            CoilOp(function_code=_FC_WRITE_COIL, coil=0, on=True),
            CoilOp(function_code=_FC_READ_COILS, coil=0, on=None),
            CoilOp(function_code=_FC_WRITE_COIL, coil=0, on=False),
        ]


async def test_start_calibration_group4_targets_coil_3() -> None:
    async with mock_modbus_pair(record_coil_ops=True) as (client, slave):
        await client.start_calibration(4)
        assert [op.coil for op in coil_ops(slave)] == [3, 3, 3]
        assert [op.function_code for op in coil_ops(slave)] == [
            _FC_WRITE_COIL,
            _FC_READ_COILS,
            _FC_WRITE_COIL,
        ]


async def test_stop_calibration_pulses_stop_all_coil_8() -> None:
    async with mock_modbus_pair(record_coil_ops=True) as (client, slave):
        await client.stop_calibration()
        assert coil_ops(slave) == [
            CoilOp(function_code=_FC_WRITE_COIL, coil=8, on=True),
            CoilOp(function_code=_FC_READ_COILS, coil=8, on=None),
            CoilOp(function_code=_FC_WRITE_COIL, coil=8, on=False),
        ]


# --- calibration_status against a live bank -------------------------------


async def test_calibration_status_active_cal_gas_1() -> None:
    async with mock_modbus_pair() as (client, slave):
        load_cal_state(slave, 1, calibrating=True, gas2=False)
        prog = await client.calibration_status(1)
        assert prog.group == 1
        assert prog.active
        assert prog.phase is CalPhase.CAL_GAS_1


async def test_calibration_status_active_cal_gas_2() -> None:
    async with mock_modbus_pair() as (client, slave):
        load_cal_state(slave, 2, calibrating=True, gas2=True)
        prog = await client.calibration_status(2)
        assert prog.group == 2
        assert prog.active
        assert prog.phase is CalPhase.CAL_GAS_2


async def test_calibration_status_idle_when_unset() -> None:
    async with mock_modbus_pair() as (client, _slave):
        prog = await client.calibration_status(3)
        assert not prog.active
        assert prog.phase is CalPhase.IDLE


# --- gates fire before any byte is sent ----------------------------------


async def test_missing_confirm_sends_no_bytes() -> None:
    async with mock_modbus_transport(record_coil_ops=True) as (transport, slave):
        async with await open_device(
            transport, protocol=ProtocolKind.MODBUS_RTU, identify=False
        ) as anz:
            with pytest.raises(ServomexConfirmationRequiredError):
                await anz.start_calibration(1)
        assert coil_ops(slave) == []  # safety gate refused pre-I/O


async def test_bad_group_sends_no_bytes() -> None:
    async with mock_modbus_transport(record_coil_ops=True) as (transport, slave):
        async with await open_device(
            transport, protocol=ProtocolKind.MODBUS_RTU, identify=False
        ) as anz:
            with pytest.raises(ServomexValidationError):
                await anz.start_calibration(7, confirm=True)
        assert coil_ops(slave) == []  # validation gate refused pre-I/O


async def test_continuous_mode_refuses_calibration() -> None:
    fake = FakeTransport()
    async with await open_device(
        fake, protocol=ProtocolKind.CONTINUOUS_ASCII, identify=False
    ) as anz:
        with pytest.raises(ServomexProtocolUnsupportedError):
            await anz.start_calibration(1, confirm=True)
