"""ServomexManager — multidrop concurrency contract."""

from __future__ import annotations

import pytest

from servomexlib.errors import ServomexValidationError
from servomexlib.manager import DeviceResult, ErrorPolicy, ServomexManager
from servomexlib.protocol.base import ProtocolKind
from servomexlib.registry.channels import ChannelId
from servomexlib.streaming import record
from servomexlib.testing import FakeTransport, mock_modbus_transport

pytestmark = pytest.mark.anyio


async def test_add_and_poll_samples_cross_port() -> None:
    async with (
        mock_modbus_transport() as (t1, _s1),
        mock_modbus_transport() as (t2, _s2),
        ServomexManager() as mgr,
    ):
        await mgr.add("a1", t1, protocol=ProtocolKind.MODBUS_RTU, address=1)
        await mgr.add("a2", t2, protocol=ProtocolKind.MODBUS_RTU, address=1)
        assert mgr.names == ("a1", "a2")
        samples = await mgr.poll_samples()
    devices = {s.device for s in samples}
    assert devices == {"a1", "a2"}
    assert any(s.channel is ChannelId.I1 for s in samples)


async def test_poll_returns_device_result_per_name() -> None:
    async with mock_modbus_transport() as (t1, _s1), ServomexManager() as mgr:
        await mgr.add("a1", t1, protocol=ProtocolKind.MODBUS_RTU, address=1)
        results = await mgr.poll()
    assert set(results) == {"a1"}
    assert isinstance(results["a1"], DeviceResult)
    assert results["a1"].ok
    assert results["a1"].value is not None


async def test_refuses_continuous_device() -> None:
    fake = FakeTransport()
    async with ServomexManager() as mgr:
        with pytest.raises(ServomexValidationError, match="multidrop"):
            await mgr.add("c1", fake, protocol=ProtocolKind.CONTINUOUS_ASCII)


async def test_duplicate_name_rejected() -> None:
    async with mock_modbus_transport() as (t1, _s1), ServomexManager() as mgr:
        await mgr.add("a1", t1, protocol=ProtocolKind.MODBUS_RTU, address=1)
        with pytest.raises(ServomexValidationError, match="already in use"):
            await mgr.add("a1", t1, protocol=ProtocolKind.MODBUS_RTU, address=1)


async def test_same_port_devices_share_one_lock() -> None:
    # Two devices added against the same transport collapse to one port entry,
    # so they serialise on a single shared lock.
    async with mock_modbus_transport() as (t1, _s1), ServomexManager() as mgr:
        await mgr.add("a", t1, protocol=ProtocolKind.MODBUS_RTU, address=1)
        await mgr.add("b", t1, protocol=ProtocolKind.MODBUS_RTU, address=1)
        lock_a = mgr._lock_for(mgr._group_by_port(("a",)).popitem()[0])
        lock_b = mgr._lock_for(mgr._group_by_port(("b",)).popitem()[0])
        assert lock_a is lock_b
        samples = await mgr.poll_samples()
    assert {s.device for s in samples} == {"a", "b"}


async def test_manager_drives_recorder() -> None:
    async with mock_modbus_transport() as (t1, _s1), ServomexManager() as mgr:
        await mgr.add("a1", t1, protocol=ProtocolKind.MODBUS_RTU, address=1)
        async with record(mgr, rate_hz=50, duration=0.1) as recording:
            batches = [batch async for batch in recording.stream]
    assert batches
    assert any(s.device == "a1" for batch in batches for s in batch)


async def test_error_policy_return_constructed() -> None:
    mgr = ServomexManager(error_policy=ErrorPolicy.RETURN)
    assert mgr.error_policy is ErrorPolicy.RETURN
    assert not mgr.closed
