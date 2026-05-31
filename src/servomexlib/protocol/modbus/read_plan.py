"""Pure Modbus block-read planning for channel sweeps.

The registry is the source of truth for channel addresses. This module turns a
set of channels into the fewest FC04/FC02 block reads allowed by a coalescing
policy, while preserving per-channel slice offsets for the decoder.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from servomexlib.protocol.modbus import registers as reg
from servomexlib.registry.channels import (
    NAME_REGISTERS,
    UNIT_REGISTERS,
    VALUE_REGISTERS,
    ChannelId,
)
from servomexlib.registry.status import DISCRETE_BITS

INPUT_CHANNEL_REGISTERS = VALUE_REGISTERS + NAME_REGISTERS + UNIT_REGISTERS


@dataclass(frozen=True, slots=True)
class ReadCoalescingPolicy:
    """Knobs controlling how aggressively adjacent channel reads are merged."""

    input_max_gap: int = 10000
    discrete_max_gap: int = 10000
    input_max_count: int = 125
    discrete_max_count: int = 2000
    fallback: bool = True

    def __post_init__(self) -> None:
        for name in ("input_max_gap", "discrete_max_gap"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be non-negative")
        for name in ("input_max_count", "discrete_max_count"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")

    @property
    def is_strict(self) -> bool:
        """Whether the policy forbids reads across address gaps."""
        return self.input_max_gap == 0 and self.discrete_max_gap == 0

    def strict(self) -> ReadCoalescingPolicy:
        """Return the conservative gap-free variant of this policy."""
        return replace(self, input_max_gap=0, discrete_max_gap=0)


DEFAULT_READ_POLICY = ReadCoalescingPolicy()
STRICT_READ_POLICY = DEFAULT_READ_POLICY.strict()


@dataclass(frozen=True, slots=True)
class BlockRead[SliceT]:
    """One contiguous Modbus block read plus slices within its response."""

    base: int
    count: int
    slices: tuple[SliceT, ...]


@dataclass(frozen=True, slots=True)
class InputChannelSlice:
    """Offsets for one channel inside an FC04 input-register block."""

    channel: ChannelId
    value_offset: int
    name_offset: int
    unit_offset: int


@dataclass(frozen=True, slots=True)
class StatusChannelSlice:
    """Offset for one channel's 8-bit status bitmap inside an FC02 block."""

    channel: ChannelId
    offset: int


@dataclass(frozen=True, slots=True)
class _ChannelSpan:
    channel: ChannelId
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class _SpanRun:
    base: int
    end: int
    spans: tuple[_ChannelSpan, ...]

    @property
    def count(self) -> int:
        return self.end - self.base


def plan_input_reads(
    channels: tuple[ChannelId, ...] | list[ChannelId],
    policy: ReadCoalescingPolicy = DEFAULT_READ_POLICY,
) -> tuple[BlockRead[InputChannelSlice], ...]:
    """Plan FC04 reads covering value/name/unit blocks for ``channels``."""
    spans = tuple(
        _ChannelSpan(
            channel=channel,
            start=reg.value_pdu(channel),
            end=reg.value_pdu(channel) + INPUT_CHANNEL_REGISTERS,
        )
        for channel in channels
    )
    reads: list[BlockRead[InputChannelSlice]] = []
    for run in _coalesce(spans, max_gap=policy.input_max_gap, max_count=policy.input_max_count):
        slices = tuple(
            InputChannelSlice(
                channel=span.channel,
                value_offset=span.start - run.base,
                name_offset=span.start - run.base + VALUE_REGISTERS,
                unit_offset=span.start - run.base + VALUE_REGISTERS + NAME_REGISTERS,
            )
            for span in run.spans
        )
        reads.append(BlockRead(base=run.base, count=run.count, slices=slices))
    return tuple(reads)


def plan_status_reads(
    channels: tuple[ChannelId, ...] | list[ChannelId],
    policy: ReadCoalescingPolicy = DEFAULT_READ_POLICY,
) -> tuple[BlockRead[StatusChannelSlice], ...]:
    """Plan FC02 reads covering 8-bit status blocks for ``channels``."""
    spans = tuple(
        _ChannelSpan(
            channel=channel,
            start=reg.status_pdu(channel),
            end=reg.status_pdu(channel) + DISCRETE_BITS,
        )
        for channel in channels
    )
    reads: list[BlockRead[StatusChannelSlice]] = []
    for run in _coalesce(
        spans,
        max_gap=policy.discrete_max_gap,
        max_count=policy.discrete_max_count,
    ):
        slices = tuple(
            StatusChannelSlice(channel=span.channel, offset=span.start - run.base)
            for span in run.spans
        )
        reads.append(BlockRead(base=run.base, count=run.count, slices=slices))
    return tuple(reads)


def _coalesce(
    spans: tuple[_ChannelSpan, ...],
    *,
    max_gap: int,
    max_count: int,
) -> tuple[_SpanRun, ...]:
    if not spans:
        return ()

    ordered = sorted(spans, key=lambda span: (span.start, span.end, span.channel.value))
    runs: list[_SpanRun] = []
    run_base = ordered[0].start
    run_end = ordered[0].end
    run_spans: list[_ChannelSpan] = [ordered[0]]

    for span in ordered[1:]:
        gap = span.start - run_end
        merged_end = max(run_end, span.end)
        if gap <= max_gap and merged_end - run_base <= max_count:
            run_end = merged_end
            run_spans.append(span)
            continue
        runs.append(_SpanRun(base=run_base, end=run_end, spans=tuple(run_spans)))
        run_base = span.start
        run_end = span.end
        run_spans = [span]

    runs.append(_SpanRun(base=run_base, end=run_end, spans=tuple(run_spans)))
    return tuple(runs)


__all__ = [
    "DEFAULT_READ_POLICY",
    "INPUT_CHANNEL_REGISTERS",
    "STRICT_READ_POLICY",
    "BlockRead",
    "InputChannelSlice",
    "ReadCoalescingPolicy",
    "StatusChannelSlice",
    "plan_input_reads",
    "plan_status_reads",
]
