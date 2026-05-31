"""Pure Modbus read-planner tests."""

from __future__ import annotations

from servomexlib.protocol.modbus.read_plan import (
    DEFAULT_READ_POLICY,
    ReadCoalescingPolicy,
    plan_input_reads,
    plan_status_reads,
)
from servomexlib.registry.channels import ChannelId

_FIVE = (ChannelId.I1, ChannelId.I2, ChannelId.I3, ChannelId.E1, ChannelId.E2)


def test_optimistic_policy_spans_bench_topology_in_one_read_per_space() -> None:
    input_reads = plan_input_reads(_FIVE, DEFAULT_READ_POLICY)
    status_reads = plan_status_reads(_FIVE, DEFAULT_READ_POLICY)

    assert [(read.base, read.count) for read in input_reads] == [(0, 70)]
    assert [(read.base, read.count) for read in status_reads] == [(0, 80)]
    assert [sl.channel for sl in input_reads[0].slices] == list(_FIVE)
    assert [sl.channel for sl in status_reads[0].slices] == list(_FIVE)


def test_strict_policy_splits_selected_channel_runs_at_gaps() -> None:
    strict = DEFAULT_READ_POLICY.strict()

    input_reads = plan_input_reads(_FIVE, strict)
    status_reads = plan_status_reads(_FIVE, strict)

    assert [(read.base, read.count) for read in input_reads] == [(0, 21), (56, 14)]
    assert [(read.base, read.count) for read in status_reads] == [(0, 24), (64, 16)]


def test_max_count_forces_splits_even_when_gap_policy_allows_merge() -> None:
    policy = ReadCoalescingPolicy(input_max_gap=10000, input_max_count=20)

    input_reads = plan_input_reads((ChannelId.I1, ChannelId.I2, ChannelId.I3), policy)

    assert [(read.base, read.count) for read in input_reads] == [(0, 14), (14, 7)]


def test_slice_offsets_recover_each_channel_window() -> None:
    input_read = plan_input_reads(_FIVE, DEFAULT_READ_POLICY)[0]
    status_read = plan_status_reads(_FIVE, DEFAULT_READ_POLICY)[0]

    input_offsets = {
        sl.channel: (sl.value_offset, sl.name_offset, sl.unit_offset) for sl in input_read.slices
    }
    status_offsets = {sl.channel: sl.offset for sl in status_read.slices}

    assert input_offsets[ChannelId.I1] == (0, 2, 5)
    assert input_offsets[ChannelId.I3] == (14, 16, 19)
    assert input_offsets[ChannelId.E1] == (56, 58, 61)
    assert status_offsets[ChannelId.I3] == 16
    assert status_offsets[ChannelId.E1] == 64
