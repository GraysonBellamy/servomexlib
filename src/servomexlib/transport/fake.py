"""In-process fake transport for hardware-free tests.

:class:`FakeTransport` is a :class:`ByteStreamTransport` whose ``receive`` is fed
from an in-memory buffer, so it drives **both** the continuous path (feed frames,
the receive loop reads them) and — once Modbus lands — ``anymodbus.Bus`` bound to
its ``ByteStream`` face. A ``script`` maps a written request to a canned reply
(the Modbus request/response seam); :meth:`feed` and :meth:`emit` push unsolicited
bytes (the continuous broadcast seam).

The inbound buffer is a plain ``bytearray`` gated by an :class:`anyio.Event`, so
there are no closable sub-resources to leak — construction is safe outside an
event loop and a never-read transport produces no ``ResourceWarning``.

Re-exported from the public :mod:`servomexlib.testing` seam.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

import anyio

from servomexlib.transport.base import ByteStreamTransport

__all__ = ["FakeTransport", "ScriptedReply"]

#: A scripted reply: bytes verbatim, a sequence concatenated, or a callable of
#: the written payload returning either.
type ScriptedReply = bytes | Sequence[bytes] | Callable[[bytes], bytes | Sequence[bytes]]


def _normalize(reply: bytes | Sequence[bytes]) -> bytes:
    return reply if isinstance(reply, bytes) else b"".join(reply)


class FakeTransport(ByteStreamTransport):
    """Scripted, in-process :class:`ByteStreamTransport`."""

    def __init__(
        self,
        script: Mapping[bytes, ScriptedReply] | None = None,
        *,
        label: str = "fake://test",
    ) -> None:
        super().__init__(label=label)
        self._script: dict[bytes, ScriptedReply] = dict(script or {})
        self._writes: list[bytes] = []
        self._inbound = bytearray()
        self._event = anyio.Event()
        self._closed = False

    # ------------------------------------------------------------------ ByteStream

    async def receive(self, max_bytes: int = 65536) -> bytes:
        while not self._inbound:
            if self._closed:
                raise anyio.EndOfStream
            await self._event.wait()
        chunk = bytes(self._inbound[:max_bytes])
        del self._inbound[:max_bytes]
        return chunk

    async def send(self, item: bytes) -> None:
        payload = bytes(item)
        self._writes.append(payload)
        reply = self._script.get(payload)
        if reply is None:
            return
        produced = reply(payload) if callable(reply) else reply
        self.feed(_normalize(produced))

    async def send_eof(self) -> None:
        self._closed = True
        self._wake()

    async def aclose(self) -> None:
        self._closed = True
        self._wake()

    @property
    def is_open(self) -> bool:
        return not self._closed

    # ------------------------------------------------------------------ test API

    @property
    def writes(self) -> tuple[bytes, ...]:
        """Every payload written through :meth:`send`, in order."""
        return tuple(self._writes)

    def feed(self, data: bytes) -> None:
        """Push unsolicited bytes into the inbound buffer."""
        self._inbound += data
        self._wake()

    def add_script(self, request: bytes, reply: ScriptedReply) -> None:
        """Register or overwrite the canned reply for ``request``."""
        self._script[bytes(request)] = reply

    async def emit(self, frames: Sequence[bytes], *, interval: float = 0.0) -> None:
        """Feed ``frames`` one at a time, optionally ``interval`` seconds apart.

        Run as a background task to simulate a continuous broadcaster:
        ``task_group.start_soon(lambda: fake.emit(frames, interval=0.05))``.
        """
        for frame in frames:
            if interval:
                await anyio.sleep(interval)
            self.feed(frame)

    # ------------------------------------------------------------------ internals

    def _wake(self) -> None:
        event = self._event
        self._event = anyio.Event()
        event.set()
