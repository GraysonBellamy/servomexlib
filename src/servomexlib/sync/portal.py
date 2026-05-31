"""A blocking portal that drives the async core from synchronous code.

:class:`SyncPortal` wraps ``anyio.from_thread.start_blocking_portal``: it runs an
event loop in a background thread and marshals calls onto it. :meth:`call` binds
keyword arguments via :func:`functools.partial` (the portal's ``call`` is
positional-only) and unwraps single-member :class:`ExceptionGroup`s so sync
callers see the concrete library error, not a wrapper.
"""

from __future__ import annotations

import functools
from typing import TYPE_CHECKING, Self, TypeVar

import anyio.from_thread

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from contextlib import AbstractAsyncContextManager, AbstractContextManager
    from types import TracebackType

T = TypeVar("T")


class SyncPortal:
    """Owns a background event loop and marshals async calls onto it."""

    def __init__(self, *, backend: str = "asyncio") -> None:
        self._backend = backend
        self._cm: AbstractContextManager[anyio.from_thread.BlockingPortal] | None = None
        self._portal: anyio.from_thread.BlockingPortal | None = None

    @property
    def running(self) -> bool:
        """Whether the backing event loop is currently running."""
        return self._portal is not None

    def __enter__(self) -> Self:
        self._cm = anyio.from_thread.start_blocking_portal(self._backend)
        self._portal = self._cm.__enter__()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool | None:
        cm, self._cm, self._portal = self._cm, None, None
        if cm is not None:
            return cm.__exit__(exc_type, exc, tb)
        return None

    def call(self, func: Callable[..., Awaitable[T]], /, *args: object, **kwargs: object) -> T:
        """Run ``func(*args, **kwargs)`` on the loop thread and return its result.

        The portal re-raises the concrete exception the coroutine raised, so sync
        callers catch the same :class:`ServomexError` subclasses as async ones.
        """
        target = functools.partial(func, *args, **kwargs)
        return self._require_portal().call(target)

    def wrap_async_context_manager(
        self, cm: AbstractAsyncContextManager[T]
    ) -> AbstractContextManager[T]:
        """Wrap an async context manager so its enter/exit run in one portal task.

        ``call(cm.__aenter__)`` then ``call(cm.__aexit__)`` would run in *different*
        loop tasks and trip anyio's cancel-scope affinity check; this bridges them.
        """
        return self._require_portal().wrap_async_context_manager(cm)

    def _require_portal(self) -> anyio.from_thread.BlockingPortal:
        if self._portal is None:
            msg = "SyncPortal is not running; use it as a context manager"
            raise RuntimeError(msg)
        return self._portal


__all__ = ["SyncPortal"]
