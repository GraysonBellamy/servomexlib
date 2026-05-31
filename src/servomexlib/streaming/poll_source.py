"""The :class:`PollSource` protocol the recorder drives.

A deliberately narrow shape: per tick, return a flat
:class:`~collections.abc.Sequence` of :class:`Sample` rows — one per channel
that was read. Both the single-device :class:`~servomexlib.devices.analyzer.Analyzer`
and the multi-device :class:`~servomexlib.manager.ServomexManager` satisfy it, so
:func:`~servomexlib.streaming.recorder.record` is unit-testable against a
lightweight stub without a transport.

Unlike the parameter-oriented siblings (watlow polls ``parameters × instances``),
the 4100 reports a *fixed set of channels* every cycle, so the contract is just
"give me this tick's samples". ``names`` selects a subset of managed devices
(manager only); a solo analyser ignores it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Sequence

    from servomexlib.streaming.sample import Sample

__all__ = ["PollSource"]


@runtime_checkable
class PollSource(Protocol):
    """Minimal shape the recorder needs from its dispatcher."""

    async def poll_samples(
        self, *, names: Sequence[str] | None = None, timeout: float | None = None
    ) -> Sequence[Sample]:
        """Read this tick's samples across every channel (and managed device).

        Args:
            names: Subset of device names to poll (manager only); ``None`` polls
                everything. A solo analyser ignores this.
            timeout: Per-poll I/O ceiling, or ``None`` for the source default.

        Returns:
            A flat sequence of :class:`Sample` — empty when every read failed.
        """
        ...
