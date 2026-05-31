"""Env-gating for the opt-in hardware test tier.

These tests are *doubly* guarded so they never run by accident:

1. The root ``pyproject.toml`` ``addopts`` deselect (``-m "not hardware and not
   hardware_stateful and not hardware_destructive"``) drops them from the default
   run; you opt in by passing ``-m hardware_stateful`` (etc.).
2. Even when explicitly selected, each marker also requires its
   ``SERVOMEXLIB_ENABLE_*`` env var to equal ``"1"`` — otherwise the item is
   skipped here. This stops a stray ``-m hardware_stateful`` on a machine with no
   bench unit from poking a (wrong) serial port.

On this dev machine CrowdStrike blocks the agent harness from spawning PowerShell
for serial access, so live runs go through Bash → Python against an environment
that has ``anyserial``/``anymodbus`` (memory ``dev-env-crowdstrike-serial``).
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Mapping

#: marker name → the env var that must be ``"1"`` for it to run.
_GATES: Mapping[str, str] = {
    "hardware": "SERVOMEXLIB_ENABLE_HARDWARE_TESTS",
    "hardware_stateful": "SERVOMEXLIB_ENABLE_STATEFUL_TESTS",
    "hardware_destructive": "SERVOMEXLIB_ENABLE_DESTRUCTIVE_TESTS",
}


def pytest_runtest_setup(item: pytest.Item) -> None:
    """Skip a hardware-marked item unless its enabling env var is set to ``"1"``.

    Gates on the item's *applied markers* (``iter_markers``), not ``item.keywords``
    — the latter also matches the parent directory name ``hardware``, which would
    make a ``hardware_stateful`` test spuriously demand the plain-``hardware`` env
    var instead of its own.
    """
    applied = {marker.name for marker in item.iter_markers()} & _GATES.keys()
    for marker in applied:
        env = _GATES[marker]
        if os.environ.get(env) != "1":
            pytest.skip(f"{marker} tests are opt-in; set {env}=1 (and connect the analyser)")
