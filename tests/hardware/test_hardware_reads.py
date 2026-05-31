"""Read-only live exercise of the full API against the bench 4100D (opt-in).

The companion to ``test_hardware_stateful.py``: where that file drives the
*write* surface (autocalibration — injects gas), this file drives **every read
and non-destructive diagnostic** and never mutates the device. It is the
executable form of ``docs/hardware-test-day.md``.

Marked ``hardware`` and gated on ``SERVOMEXLIB_ENABLE_HARDWARE_TESTS=1`` (see
``conftest.py``); never runs in CI.

Run (from an env with ``anyserial``/``anymodbus`` — on this dev machine that is
the project's own ``.venv`` driven via Bash, *not* the PowerShell tool, because
CrowdStrike blocks its serial spawn path)::

    SERVOMEXLIB_ENABLE_HARDWARE_TESTS=1 \\
    SERVOMEXLIB_HARDWARE_PORT=COM11 SERVOMEXLIB_HARDWARE_ADDRESS=30 \\
        .venv/Scripts/python.exe -m pytest tests/hardware -m hardware -v

The analyser is in exactly **one** comm mode at a time (the three modes are
mutually exclusive on one port — switch it on the front panel). Tell the suite
which mode the unit is in via ``SERVOMEXLIB_HARDWARE_MODE``
(``modbus_rtu`` *(default)* / ``modbus_ascii`` / ``continuous``); tests for the
other modes skip themselves.

Numeric expectations are kept as *sanity ranges*, not exact concentrations — the
sample gas on any given test day is unknown. The known idle values (O₂ 20.378 %,
CO 0.084 %, CO₂ 0.250 %) are noted in comments as the cross-check baseline.
"""

from __future__ import annotations

import math
import os
from typing import TYPE_CHECKING

import anyio
import pytest

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

from servomexlib import (
    Analyzer,
    CalibrationProgress,
    CalPhase,
    Capability,
    ChannelId,
    ProtocolKind,
    Reading,
    Sample,
    ServomexConfirmationRequiredError,
    ServomexConnectionError,
    ServomexManager,
    ServomexProtocolUnsupportedError,
    ServomexTimeoutError,
    ServomexValidationError,
    Unit,
    open_continuous,
    open_device,
    record,
)
from servomexlib.protocol.modbus.client import ModbusClient

pytestmark = [pytest.mark.anyio, pytest.mark.hardware]

# --- bench configuration (env-overridable; defaults = the bench 4100D) -------
_PORT = os.environ.get("SERVOMEXLIB_HARDWARE_PORT", "COM11")
_ADDRESS = int(os.environ.get("SERVOMEXLIB_HARDWARE_ADDRESS", "30"))
# Per-attempt request_timeout passed to open_device (anymodbus bounds each attempt
# and retries internally). Reliability comes from the session's ~50 ms
# inter_frame_idle — the 4000-series drops rapid back-to-back reads at the RTU-spec
# t3.5 — not from this ceiling, so 1.0s is plenty.
_TIMEOUT = float(os.environ.get("SERVOMEXLIB_HARDWARE_TIMEOUT", "1.0"))
_MODE = os.environ.get("SERVOMEXLIB_HARDWARE_MODE", "modbus_rtu").lower()
_PROTOCOL = ProtocolKind(_MODE)

_IS_MODBUS = _MODE in {"modbus_rtu", "modbus_ascii"}
_IS_CONTINUOUS = _MODE == "continuous"

#: Concentration units an I/D channel may report (E channels report mA).
_CONCENTRATION_UNITS = {Unit.PERCENT, Unit.VPM, Unit.PPM}

#: The 4100's continuous broadcast period is operator-configurable (1–999 s on the
#: front panel). Tests can't assume a value, so set SERVOMEXLIB_HARDWARE_BROADCAST_S
#: to the unit's configured interval (default 2 s). Continuous-mode reads and the
#: AUTO listen window then allow several periods of head-room.
_BROADCAST_S = float(os.environ.get("SERVOMEXLIB_HARDWARE_BROADCAST_S", "2.0"))
_CONTINUOUS_FRAME_TIMEOUT = _BROADCAST_S * 3.0 + 2.0

modbus_only = pytest.mark.skipif(
    not _IS_MODBUS, reason=f"unit is in {_MODE!r} mode; set SERVOMEXLIB_HARDWARE_MODE=modbus_rtu"
)
continuous_only = pytest.mark.skipif(
    not _IS_CONTINUOUS,
    reason=f"unit is in {_MODE!r} mode; set SERVOMEXLIB_HARDWARE_MODE=continuous",
)


@pytest.fixture
def anyio_backend() -> str:
    """Pin live hardware tests to one backend so the port opens once per test."""
    return "asyncio"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


#: Back-to-back tests each open/close the exclusive COM port; Windows can take a
#: moment to release the handle, so a fresh open may hit "Access is denied". Retry
#: a few times with a short settle to absorb that release latency.
_OPEN_RETRIES = 6
_OPEN_SETTLE_S = 0.4


async def _retry_connect[T](factory: Callable[[], Awaitable[T]]) -> T:
    """Run ``factory`` (an open call), retrying on a transient port-busy open."""
    last: ServomexConnectionError | None = None
    for _ in range(_OPEN_RETRIES):
        try:
            return await factory()
        except ServomexConnectionError as exc:
            last = exc
            await anyio.sleep(_OPEN_SETTLE_S)
    assert last is not None
    raise last


async def _open_modbus(*, address: int = _ADDRESS, identify: bool = True) -> Analyzer:
    return await _retry_connect(
        lambda: open_device(
            _PORT, protocol=_PROTOCOL, address=address, timeout=_TIMEOUT, identify=identify
        )
    )


async def _open_continuous() -> Analyzer:
    return await _retry_connect(lambda: open_continuous(_PORT, timeout=_TIMEOUT))


def _assert_reading_sane(reading: Reading) -> None:
    """A populated channel reads a finite value in a plausible unit."""
    if reading.value is not None:  # None == over-range / invalid, which is legal
        assert math.isfinite(reading.value)
    if reading.channel.value.startswith("E"):
        assert reading.unit is Unit.MILLIAMP
    else:
        # I/D channels are concentrations (UNKNOWN tolerated for an empty slot).
        assert reading.unit in _CONCENTRATION_UNITS or reading.unit is Unit.UNKNOWN


# ========================================================================== #
# Session A — Modbus RTU (primary)                                            #
# ========================================================================== #


@modbus_only
async def test_identify_reports_topology() -> None:
    """``identify`` returns the populated slots, address, and Modbus capabilities."""
    async with await _open_modbus() as anz:
        info = await anz.identify(timeout=_TIMEOUT)
        assert info.address == _ADDRESS
        assert info.protocol is _PROTOCOL
        channels = {c.channel for c in info.channels}
        # I1..I3 are populated on the bench unit (O2 / CO / CO2).
        assert {ChannelId.I1, ChannelId.I2, ChannelId.I3} <= channels
        # Modbus advertises the full capability set.
        assert Capability.AUTOCAL in anz.capabilities
        assert Capability.LOOPBACK in anz.capabilities


@modbus_only
async def test_identify_decodes_charset() -> None:
    """The CO₂ name decodes through the display charset (subscript glyph, not garbage)."""
    async with await _open_modbus() as anz:
        info = await anz.identify(timeout=_TIMEOUT)
        names = {c.channel: c.name for c in info.channels}
        co2 = names.get(ChannelId.I3)
        # Modbus sends 0x82 for the subscript 2 → "CO₂"; never the raw byte.
        assert co2 is not None
        assert "\x82" not in co2


@modbus_only
async def test_poll_full_frame() -> None:
    """A single ``poll`` returns every populated channel with sane values/units."""
    async with await _open_modbus() as anz:
        frame = await anz.poll(timeout=_TIMEOUT)
        assert frame.protocol is _PROTOCOL
        by_id = {r.channel: r for r in frame.readings}
        assert {ChannelId.I1, ChannelId.I2, ChannelId.I3} <= by_id.keys()
        for reading in frame.readings:
            _assert_reading_sane(reading)
        # Baseline cross-check (sample-gas dependent): O2≈20.378, CO≈0.084, CO2≈0.250.
        o2 = by_id[ChannelId.I1].value
        assert o2 is not None
        assert 0.0 <= o2 <= 100.0


@modbus_only
async def test_read_all_matches_channel_reads() -> None:
    """``read_all`` and per-channel ``read_channel`` agree on the populated slots."""
    async with await _open_modbus() as anz:
        every = await anz.read_all(timeout=_TIMEOUT)
        assert {ChannelId.I1, ChannelId.I2, ChannelId.I3} <= every.keys()
        for cid in (ChannelId.I1, ChannelId.I2, ChannelId.I3):
            single = await anz.read_channel(cid, timeout=_TIMEOUT)
            assert single.channel is cid
            _assert_reading_sane(single)


@modbus_only
async def test_status_and_analyser_status() -> None:
    """Per-channel and analyser-level status decode into the typed flags."""
    async with await _open_modbus() as anz:
        for cid in (ChannelId.I1, ChannelId.I2, ChannelId.I3):
            st = await anz.status(cid, timeout=_TIMEOUT)
            assert isinstance(st.fault, bool)
            assert len(st.alarms) == 4
            assert isinstance(st.ok, bool)
        analyser = await anz.analyser_status(timeout=_TIMEOUT)
        assert isinstance(analyser.fault, bool)
        assert isinstance(analyser.cal_groups, tuple)


@modbus_only
async def test_external_input_status_uses_invalid_not_fault() -> None:
    """E1/E2 map bit 0 to ``Invalid`` — an idle mA input must read ``ok``."""
    async with await _open_modbus() as anz:
        info = await anz.identify(timeout=_TIMEOUT)
        present = {c.channel for c in info.channels}
        if ChannelId.E1 not in present:
            pytest.skip("E1 not populated on this unit")
        reading = await anz.read_channel(ChannelId.E1, timeout=_TIMEOUT)
        assert reading.unit is Unit.MILLIAMP
        # Idle analogue input is valid → ok; bit 0 must not be read as a hard fault.
        assert reading.status.ok


@modbus_only
async def test_snapshot_returns_cached_frame() -> None:
    """``snapshot`` returns the last frame with no I/O after a poll."""
    async with await _open_modbus() as anz:
        await anz.poll(timeout=_TIMEOUT)
        cached = anz.snapshot()
        assert cached.readings


@modbus_only
async def test_snapshot_before_first_poll_raises() -> None:
    """``snapshot`` before any frame is cached raises (no silent empty frame)."""
    async with await _open_modbus(identify=False) as anz:
        with pytest.raises(ServomexValidationError):
            anz.snapshot()


@modbus_only
async def test_calibration_status_is_read_only() -> None:
    """``calibration_status`` for groups 1–4 reads progress without mutating anything."""
    async with await _open_modbus() as anz:
        for group in (1, 2, 3, 4):
            prog = await anz.calibration_status(group, timeout=_TIMEOUT)
            assert isinstance(prog, CalibrationProgress)
            assert prog.group == group
            assert isinstance(prog.phase, CalPhase)
            assert isinstance(prog.active, bool)


@modbus_only
async def test_loopback_echoes_payload() -> None:
    """FC08 diagnostic loopback echoes its payload (non-destructive)."""
    async with await _open_modbus() as anz:
        if Capability.LOOPBACK not in anz.capabilities:
            pytest.skip("active client has no LOOPBACK capability")
        payload = b"\xa5\x5a"
        # loopback lives on the Modbus client, below the protocol-neutral facade.
        client = anz._client
        assert isinstance(client, ModbusClient)
        echoed = await client.loopback(payload, timeout=_TIMEOUT)
        assert echoed == payload


@modbus_only
async def test_cold_start_first_transaction_succeeds() -> None:
    """The RS485 turnaround timeout-then-retry is absorbed: the first poll succeeds."""
    async with await _open_modbus() as anz:
        frame = await anz.poll(timeout=_TIMEOUT)  # first txn after open
        assert frame.readings


@modbus_only
async def test_poll_stream_yields_samples() -> None:
    """``stream`` (POLL mode) yields long-format samples at the requested rate."""
    got: list[Sample] = []
    async with await _open_modbus() as anz:
        await anz.poll(timeout=_TIMEOUT)  # absorb the cold-start retry before timing
        async with anz.stream(rate_hz=4.0) as samples:
            with anyio.move_on_after(6.0):
                async for sample in samples:
                    got.append(sample)
                    if len(got) >= 5:
                        break
    assert got
    assert any(s.reading is not None for s in got)


@modbus_only
async def test_record_drift_free_acquisition() -> None:
    """``record`` produces batches and a populated :class:`AcquisitionSummary`."""
    async with await _open_modbus() as anz:
        await anz.poll(timeout=_TIMEOUT)  # warm the connection before the timed run
        async with record(anz, rate_hz=2.0, duration=3.0) as recording:
            batches = [batch async for batch in recording.stream]
    assert recording.summary.samples_emitted > 0
    assert recording.summary.max_drift_ms >= 0.0
    assert batches  # at least one tick delivered


@modbus_only
async def test_manager_single_device() -> None:
    """A one-device manager polls samples and runs ``execute_each``."""
    async with ServomexManager() as mgr:
        await _retry_connect(
            lambda: mgr.add("a1", _PORT, protocol=_PROTOCOL, address=_ADDRESS, timeout=_TIMEOUT)
        )
        samples = await mgr.poll_samples(timeout=_TIMEOUT)
        assert samples
        assert all(isinstance(s, Sample) for s in samples)
        results = await mgr.execute_each(lambda a: a.identify(timeout=_TIMEOUT))
        assert results["a1"].ok


# --- error-path reads (gates fire before I/O; no device mutation) ---------- #


@modbus_only
async def test_start_calibration_without_confirm_is_gated() -> None:
    """``start_calibration`` without ``confirm`` raises *before* any byte is sent."""
    async with await _open_modbus() as anz:
        with pytest.raises(ServomexConfirmationRequiredError):
            await anz.start_calibration(1)  # confirm defaults to False → no I/O


@modbus_only
async def test_unknown_channel_raises_validation() -> None:
    """A bad channel id is rejected before any I/O."""
    async with await _open_modbus() as anz:
        with pytest.raises(ServomexValidationError):
            await anz.read_channel("Z9", timeout=_TIMEOUT)


@modbus_only
async def test_wrong_address_times_out() -> None:
    """Polling a non-existent slave address times out (non-destructive)."""
    async with await _open_modbus(address=_ADDRESS + 1, identify=False) as anz:
        with pytest.raises(ServomexTimeoutError):
            await anz.poll(timeout=_TIMEOUT)


# ========================================================================== #
# Session B — Continuous ASCII (front-panel switch to broadcast)             #
# ========================================================================== #


@continuous_only
async def test_continuous_poll_returns_latest_frame() -> None:
    """The passive client serves the latest broadcast frame on ``poll``."""
    async with await _open_continuous() as anz:
        frame = await anz.poll(timeout=_CONTINUOUS_FRAME_TIMEOUT)
        assert frame.protocol is ProtocolKind.CONTINUOUS_ASCII
        assert frame.readings


@continuous_only
async def test_continuous_wait_fresh_advances() -> None:
    """``wait_fresh=True`` returns the *next* broadcast, not the cached one."""
    async with await _open_continuous() as anz:
        first = await anz.poll(timeout=_CONTINUOUS_FRAME_TIMEOUT)
        fresh = await anz.poll(wait_fresh=True, timeout=_CONTINUOUS_FRAME_TIMEOUT)
        assert fresh.monotonic_ns >= first.monotonic_ns


@continuous_only
async def test_continuous_capabilities_are_read_only() -> None:
    """Continuous advertises read/identify only — no AUTOCAL, no LOOPBACK."""
    async with await _open_continuous() as anz:
        assert Capability.READ_CHANNELS in anz.capabilities
        assert Capability.AUTOCAL not in anz.capabilities
        assert Capability.LOOPBACK not in anz.capabilities
        assert isinstance(anz.dropped_frames, int)


@continuous_only
async def test_continuous_stream_passive_subscribe() -> None:
    """AUTOPRINT streaming yields samples with no request provenance (passive)."""
    got: list[Sample] = []
    async with await _open_continuous() as anz, anz.stream() as samples:
        with anyio.move_on_after(_CONTINUOUS_FRAME_TIMEOUT):
            async for sample in samples:
                got.append(sample)
                if len(got) >= 3:
                    break
    assert got
    # Passive mode never issued a request, so latency is unmeasured.
    assert all(s.latency_s is None for s in got if s.reading is not None)


@continuous_only
async def test_continuous_autocal_is_capability_gated() -> None:
    """``start_calibration`` in continuous mode fails at the capability gate (pre-I/O)."""
    async with await _open_continuous() as anz:
        with pytest.raises(ServomexProtocolUnsupportedError):
            await anz.start_calibration(1, confirm=True)


# ========================================================================== #
# Session C — AUTO detection                                                  #
# ========================================================================== #


async def test_auto_detects_current_mode() -> None:
    """``AUTO`` resolves to whatever mode the unit is physically in."""
    # In continuous mode the sniffer must listen past one broadcast period.
    auto_timeout = _CONTINUOUS_FRAME_TIMEOUT if _IS_CONTINUOUS else _TIMEOUT
    auto = await _retry_connect(
        lambda: open_device(
            _PORT, protocol=ProtocolKind.AUTO, address=_ADDRESS, timeout=auto_timeout
        )
    )
    async with auto as anz:
        if _IS_MODBUS:
            assert anz.protocol in {ProtocolKind.MODBUS_RTU, ProtocolKind.MODBUS_ASCII}
        elif _IS_CONTINUOUS:
            assert anz.protocol is ProtocolKind.CONTINUOUS_ASCII
        else:  # pragma: no cover - defensive
            pytest.skip(f"unknown SERVOMEXLIB_HARDWARE_MODE={_MODE!r}")
