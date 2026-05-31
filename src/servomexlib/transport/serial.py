"""Real serial transport over ``anyserial``.

:class:`SerialTransport` wraps an :class:`anyserial.SerialPort` — itself an
``anyio.abc.ByteStream`` — forwarding the four stream methods and inheriting the
framing helpers from :class:`ByteStreamTransport`. ``reopen`` re-creates the port
with new settings (``anyserial`` has no in-place reconfigure).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from anyserial import SerialConfig, open_serial_port

from servomexlib.errors import ErrorContext, ServomexConnectionError
from servomexlib.transport.base import ByteStreamTransport, SerialSettings

if TYPE_CHECKING:
    from anyserial import SerialPort


class SerialTransport(ByteStreamTransport):
    """A :class:`ByteStreamTransport` backed by a live ``anyserial`` port."""

    def __init__(self, port: SerialPort, settings: SerialSettings) -> None:
        super().__init__(label=settings.port)
        self._port = port
        self._settings = settings
        self._open = True

    @classmethod
    async def open(cls, settings: SerialSettings) -> SerialTransport:
        """Open ``settings.port`` and return a connected transport."""
        try:
            port = await open_serial_port(settings.port, _config_for(settings))
        except OSError as exc:  # pragma: no cover - needs real hardware
            raise ServomexConnectionError(
                f"could not open {settings.port}: {exc}",
                context=ErrorContext(port=settings.port),
            ) from exc
        return cls(port, settings)

    @property
    def is_open(self) -> bool:
        """Whether the underlying serial port is still open."""
        return self._open

    @property
    def settings(self) -> SerialSettings:
        """The serial settings the port is currently configured with."""
        return self._settings

    async def receive(self, max_bytes: int = 65536) -> bytes:
        return await self._port.receive(max_bytes)

    async def send(self, item: bytes) -> None:
        await self._port.send(item)

    async def send_eof(self) -> None:
        await self._port.send_eof()

    async def aclose(self) -> None:
        if not self._open:
            return  # idempotent: discovery/session may both close the shared port
        self._open = False
        await self._port.aclose()

    async def reopen(self, settings: SerialSettings) -> None:
        """Close the current port and reopen it with ``settings`` (re-config)."""
        await self._port.aclose()
        self._port = await open_serial_port(settings.port, _config_for(settings))
        self._settings = settings
        self._open = True
        await self.drain_input()


def _config_for(settings: SerialSettings) -> SerialConfig:
    return SerialConfig(
        baudrate=settings.baudrate,
        byte_size=settings.bytesize,
        parity=settings.parity,
        stop_bits=settings.stopbits,
        exclusive=settings.exclusive,
    )


__all__ = ["SerialTransport"]
