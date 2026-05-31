"""ContinuousClient + Analyzer: replay, wait_fresh, resync, fan-out, gates.

Async tests run across the full anyio backend matrix via the parametrised
``anyio_backend`` fixture in conftest.
"""

from __future__ import annotations

import pytest

from servomexlib.devices.factory import open_continuous
from servomexlib.errors import ServomexTimeoutError, ServomexValidationError
from servomexlib.registry.channels import ChannelId
from servomexlib.streaming.stream_session import StreamMode
from servomexlib.testing import FakeTransport, split_continuous_frames
from tests.conftest import approx

pytestmark = pytest.mark.anyio


def _frames(capture: bytes) -> list[bytes]:
    return [frame + b"\r\n" for frame in split_continuous_frames(capture)]


async def test_poll_returns_latest_cached_frame(continuous_capture: bytes) -> None:
    fake = FakeTransport()
    async with await open_continuous(fake, device="bench") as anz:
        for chunk in _frames(continuous_capture):
            fake.feed(chunk)
        result = await anz.read_channel("I1")
        assert result.name == "Oxygen"
        assert result.value == approx(20.376)
        frame = await anz.poll()
        assert frame.readings[0].channel is ChannelId.I1
        assert anz.snapshot() is frame  # cached, no I/O


async def test_read_all_and_status(continuous_capture: bytes) -> None:
    fake = FakeTransport()
    async with await open_continuous(fake) as anz:
        fake.feed(_frames(continuous_capture)[0])
        readings = await anz.read_all()
        assert set(readings) == {
            ChannelId.I1,
            ChannelId.I2,
            ChannelId.I3,
            ChannelId.E1,
            ChannelId.E2,
        }
        assert (await anz.status("I1")).ok is True
        assert (await anz.analyser_status()).fault is False


async def test_wait_fresh_waits_for_next_frame(continuous_capture: bytes) -> None:
    frames = _frames(continuous_capture)
    fake = FakeTransport()
    async with await open_continuous(fake) as anz:
        fake.feed(frames[0])
        first = await anz.poll()
        fake.feed(frames[1])
        nxt = await anz.poll(wait_fresh=True, timeout=2)
        assert nxt is not first
        assert nxt.analyser.clock != first.analyser.clock


async def test_first_frame_timeout_raises() -> None:
    fake = FakeTransport()
    async with await open_continuous(fake, timeout=0.05) as anz:
        with pytest.raises(ServomexTimeoutError):
            await anz.poll()


async def test_snapshot_before_first_frame_raises() -> None:
    fake = FakeTransport()
    async with await open_continuous(fake) as anz:
        with pytest.raises(ServomexValidationError):
            anz.snapshot()


async def test_corrupt_frame_surfaces_error_sample_and_survives(continuous_capture: bytes) -> None:
    frames = _frames(continuous_capture)
    fake = FakeTransport()
    async with await open_continuous(fake) as anz, anz.stream() as stream:
        fake.feed(b" garbage;frame;\r\n")
        fake.feed(frames[0])
        seen: list[str] = []
        async for sample in stream:
            if sample.error is not None or sample.channel is None:
                seen.append("ERR")
            else:
                seen.append(sample.channel.value)
            if "I1" in seen:
                break
        assert seen[0] == "ERR"
        assert "I1" in seen
        assert anz.dropped_frames == 1


async def test_stream_multi_subscriber_fan_out(continuous_capture: bytes) -> None:
    frame0 = _frames(continuous_capture)[0]
    fake = FakeTransport()
    async with await open_continuous(fake) as anz, anz.stream() as a, anz.stream() as b:
        fake.feed(frame0)
        got_a = [await a.__anext__() for _ in range(5)]
        got_b = [await b.__anext__() for _ in range(5)]
        assert [s.channel for s in got_a] == [s.channel for s in got_b]
        assert len(got_a) == 5


async def test_stream_poll_mode_rejected() -> None:
    fake = FakeTransport()
    async with await open_continuous(fake) as anz:
        with pytest.raises(ServomexValidationError):
            anz.stream(mode=StreamMode.POLL)


async def test_identify_reports_populated_slots(continuous_capture: bytes) -> None:
    fake = FakeTransport()
    async with await open_continuous(fake) as anz:
        fake.feed(_frames(continuous_capture)[0])
        info = await anz.identify()
        assert info.protocol.value == "continuous"
        assert [c.channel for c in info.channels][:3] == [ChannelId.I1, ChannelId.I2, ChannelId.I3]
        assert info.channels[0].name == "Oxygen"
