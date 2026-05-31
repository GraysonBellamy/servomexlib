"""Streaming session + mode enum.

``StreamMode`` maps onto the protocols: continuous → ``AUTOPRINT`` (passive
broadcast subscribe), Modbus → ``POLL`` (timed acquisition). The facade defaults
the right mode per protocol; passing the wrong one raises
:class:`ServomexValidationError`.

:class:`StreamingSession` is an async-iterable context manager over one
subscriber's :class:`Sample` stream; closing it unsubscribes.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Self

import anyio

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from types import TracebackType

    from anyio.abc import TaskGroup
    from anyio.streams.memory import MemoryObjectReceiveStream

    from servomexlib.streaming.sample import Sample


class StreamMode(StrEnum):
    """How a :meth:`Analyzer.stream` session sources samples.

    ``AUTOPRINT`` is the inherited family member (sartorius SBI vocabulary),
    reused verbatim for boundary harmony; for the 4100 it denotes a passive
    **unsolicited-broadcast** subscribe.
    """

    POLL = "poll"
    AUTOPRINT = "autoprint"


class StreamingSession:
    """Async-iterable context manager over one subscriber's :class:`Sample` stream.

    Two backing shapes share this one interface:

    - **Passive ``AUTOPRINT``** (continuous): ``receiver`` is fed by the client's
      already-running background loop; ``on_close`` unsubscribes.
    - **Active ``POLL``** (Modbus): a ``producer`` coroutine is supplied. It is run
      in a task group this session owns — started on ``__aenter__`` and cancelled
      on close — so the recorder's lifetime is strictly nested in the session.
    """

    def __init__(
        self,
        receiver: MemoryObjectReceiveStream[Sample],
        *,
        mode: StreamMode,
        on_close: Callable[[], None] | None = None,
        producer: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._receiver = receiver
        self._mode = mode
        self._on_close = on_close
        self._producer = producer
        self._task_group: TaskGroup | None = None

    @property
    def mode(self) -> StreamMode:
        """The mode this session is streaming in."""
        return self._mode

    async def __aenter__(self) -> Self:
        if self._producer is not None:
            task_group = anyio.create_task_group()
            await task_group.__aenter__()
            task_group.start_soon(self._producer)
            self._task_group = task_group
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Stop the owned producer (if any), unsubscribe, and close the stream."""
        if self._task_group is not None:
            self._task_group.cancel_scope.cancel()
            await self._task_group.__aexit__(None, None, None)
            self._task_group = None
        if self._on_close is not None:
            self._on_close()
            self._on_close = None
        await self._receiver.aclose()

    def __aiter__(self) -> Self:
        return self

    async def __anext__(self) -> Sample:
        try:
            return await self._receiver.receive()
        except (anyio.EndOfStream, anyio.ClosedResourceError) as exc:
            raise StopAsyncIteration from exc


__all__ = ["StreamMode", "StreamingSession"]
