"""FakeTransport: framing helpers, pushback, ByteStream face, scripted replies."""

from __future__ import annotations

import pytest
from anyio.abc import ByteStream

from servomexlib.transport.base import SerialSettings, Transport
from servomexlib.transport.fake import FakeTransport

pytestmark = pytest.mark.anyio


def test_serial_settings_defaults() -> None:
    settings = SerialSettings(port="COM11")
    assert settings.baudrate == 19200
    assert settings.parity.value == "none"
    assert settings.stopbits.value == "1"
    assert settings.bytesize.value == "8"


def test_is_bytestream_and_transport() -> None:
    fake = FakeTransport()
    assert isinstance(fake, ByteStream)
    assert isinstance(fake, Transport)


async def test_read_until_and_exact() -> None:
    fake = FakeTransport()
    fake.feed(b"hello;world;\r\n")
    assert await fake.read_until(b";", timeout=1) == b"hello;"
    assert await fake.read_exact(5, timeout=1) == b"world"


async def test_pushback_prepends() -> None:
    fake = FakeTransport()
    fake.feed(b"ABCDEF")
    first = await fake.read_exact(3, timeout=1)
    assert first == b"ABC"
    fake.pushback(first)
    assert await fake.read_exact(6, timeout=1) == b"ABCDEF"


async def test_read_until_times_out_when_no_separator() -> None:
    from servomexlib.errors import ServomexTimeoutError

    fake = FakeTransport()
    fake.feed(b"no terminator here")
    with pytest.raises(ServomexTimeoutError):
        await fake.read_until(b"\r\n", timeout=0.05)


async def test_bytestream_receive_send_and_writes() -> None:
    fake = FakeTransport()
    fake.feed(b"abc")
    assert await fake.receive(2) == b"ab"
    assert await fake.receive(10) == b"c"  # overflow handed back, then drained
    await fake.send(b"request")
    assert fake.writes == (b"request",)


async def test_scripted_reply_feeds_inbound() -> None:
    fake = FakeTransport({b"\x01\x02": b"\x03\x04"})
    await fake.send(b"\x01\x02")
    assert await fake.read_exact(2, timeout=1) == b"\x03\x04"


async def test_read_available_drains_within_idle_window() -> None:
    fake = FakeTransport()
    fake.feed(b"partial")
    got = await fake.read_available(idle_timeout=0.05)
    assert got == b"partial"
