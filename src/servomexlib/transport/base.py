"""Transport interface + buffered base.

The 4100 has three mutually-exclusive modes on one port and the ``AUTO`` ladder
must sniff raw bytes before it knows the protocol, so a single uniform transport
backs all three. Critically, a transport **must satisfy ``anyio.abc.ByteStream``**
(``receive`` / ``send`` / ``send_eof`` / ``aclose``) so ``anymodbus.Bus`` can bind
to it — the family convenience helpers (``read_exact`` / ``read_until`` /
``read_available`` / ``write``) are a *superset* layered on top, not an
alternative interface.

:class:`ByteStreamTransport` provides that superset (plus a pushback buffer for
the single-reader discipline) on top of the four abstract ``ByteStream`` methods;
:class:`~servomexlib.transport.serial.SerialTransport` and
:class:`~servomexlib.transport.fake.FakeTransport` implement those four.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, Self, runtime_checkable

import anyio
from anyio.abc import ByteStream
from anyserial import ByteSize, Parity, StopBits

from servomexlib.errors import ErrorContext, ServomexConnectionError, ServomexTimeoutError

if TYPE_CHECKING:
    from types import TracebackType

#: Inclusive valid baud range for the 4000-series serial port.
MIN_BAUD = 2400
MAX_BAUD = 19200

_DEFAULT_CHUNK = 65536


@dataclass(frozen=True, slots=True)
class SerialSettings:
    """Frozen serial framing descriptor. Default ``19200 / 8-N-1``."""

    port: str
    baudrate: int = MAX_BAUD
    bytesize: ByteSize = ByteSize.EIGHT
    parity: Parity = Parity.NONE
    stopbits: StopBits = StopBits.ONE
    rtscts: bool = False
    xonxoff: bool = False
    exclusive: bool = True


@runtime_checkable
class Transport(Protocol):
    """Structural interface the rest of the library depends on.

    Every concrete transport is also an ``anyio.abc.ByteStream`` (it carries the
    ``receive`` / ``send`` / ``send_eof`` / ``aclose`` face), so it can be handed
    straight to ``anymodbus.Bus`` while still exposing the framing helpers the
    continuous path uses.
    """

    @property
    def label(self) -> str: ...
    @property
    def is_open(self) -> bool: ...

    async def receive(self, max_bytes: int = ...) -> bytes: ...
    async def send(self, item: bytes) -> None: ...
    async def send_eof(self) -> None: ...
    async def aclose(self) -> None: ...

    async def read_exact(self, n: int, *, timeout: float) -> bytes: ...
    async def read_until(self, separator: bytes, *, timeout: float) -> bytes: ...
    async def read_available(
        self, *, idle_timeout: float, max_bytes: int | None = ...
    ) -> bytes: ...
    async def write(self, data: bytes, *, timeout: float) -> None: ...

    def pushback(self, data: bytes) -> None: ...
    async def drain_input(self) -> None: ...


class ByteStreamTransport(ByteStream):
    """``ByteStream`` + the family framing helpers and a pushback buffer.

    Subclasses implement the four ``ByteStream`` abstract methods (``receive`` /
    ``send`` / ``send_eof`` / ``aclose``); this base layers ``read_exact`` /
    ``read_until`` / ``read_available`` / ``write`` on top, draining a pushback
    buffer before pulling fresh bytes (the single-reader discipline).
    """

    def __init__(self, *, label: str) -> None:
        self._label = label
        self._buf = bytearray()

    @property
    def label(self) -> str:
        """Human-readable transport identifier used in errors."""
        return self._label

    @property
    def is_open(self) -> bool:
        """Whether the transport is currently usable for I/O."""
        return True

    # ------------------------------------------------------------------ helpers

    def pushback(self, data: bytes) -> None:
        """Return ``data`` to the front of the read buffer (single-reader seam)."""
        self._buf[:0] = data

    async def drain_input(self) -> None:
        """Discard any buffered/pending input so the next read starts clean."""
        self._buf.clear()

    async def read_exact(self, n: int, *, timeout: float) -> bytes:
        """Read exactly ``n`` bytes within ``timeout`` seconds."""
        try:
            with anyio.fail_after(timeout):
                while len(self._buf) < n:
                    self._buf += await self._recv_chunk()
        except TimeoutError as exc:
            raise self._timeout_error("read_exact", timeout) from exc
        return self._take(n)

    async def read_until(self, separator: bytes, *, timeout: float) -> bytes:
        """Read until (and including) ``separator`` within ``timeout`` seconds."""
        try:
            with anyio.fail_after(timeout):
                idx = self._buf.find(separator)
                while idx < 0:
                    self._buf += await self._recv_chunk()
                    idx = self._buf.find(separator)
        except TimeoutError as exc:
            raise self._timeout_error("read_until", timeout) from exc
        return self._take(idx + len(separator))

    async def read_available(
        self,
        *,
        idle_timeout: float,
        max_bytes: int | None = None,
    ) -> bytes:
        """Read whatever arrives until an ``idle_timeout`` gap or ``max_bytes``."""
        with anyio.move_on_after(idle_timeout):
            while max_bytes is None or len(self._buf) < max_bytes:
                try:
                    self._buf += await self._recv_chunk()
                except ServomexConnectionError:
                    break
        count = len(self._buf) if max_bytes is None else min(max_bytes, len(self._buf))
        return self._take(count)

    async def write(self, data: bytes, *, timeout: float) -> None:
        """Send ``data`` within ``timeout`` seconds."""
        try:
            with anyio.fail_after(timeout):
                await self.send(data)
        except TimeoutError as exc:
            raise self._timeout_error("write", timeout) from exc

    # ------------------------------------------------------------------ internals

    async def _recv_chunk(self) -> bytes:
        try:
            return await self.receive(_DEFAULT_CHUNK)
        except anyio.EndOfStream as exc:
            raise ServomexConnectionError(
                f"{self._label} reached end of stream",
                context=ErrorContext(port=self._label),
            ) from exc
        except anyio.ClosedResourceError as exc:
            raise ServomexConnectionError(
                f"{self._label} is closed",
                context=ErrorContext(port=self._label),
            ) from exc

    def _take(self, count: int) -> bytes:
        out = bytes(self._buf[:count])
        del self._buf[:count]
        return out

    def _timeout_error(self, op: str, timeout: float) -> ServomexTimeoutError:
        return ServomexTimeoutError(
            f"{op} on {self._label} timed out after {timeout}s",
            context=ErrorContext(port=self._label, extra={"op": op}),
        )

    # ------------------------------------------------------------------ async CM

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()


__all__ = [
    "MAX_BAUD",
    "MIN_BAUD",
    "ByteStreamTransport",
    "SerialSettings",
    "Transport",
]
