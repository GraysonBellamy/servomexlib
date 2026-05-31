"""Destructive-op gate for ``servomex-diag`` subcommands.

The current diagnostics (loopback, tap, jitter) are all read-only, but the gate is
kept so any future state-changing subcommand can require an explicit
acknowledgement flag — failing with exit code ``2`` (CI-parseable) otherwise.
"""

from __future__ import annotations

import sys

__all__ = ["DESTRUCTIVE_FLAG", "require_destructive_ack"]

#: The flag a destructive subcommand must receive to proceed.
DESTRUCTIVE_FLAG = "--i-understand-this-is-destructive"


def require_destructive_ack(acked: bool, *, op: str) -> None:
    """Exit ``2`` unless ``acked`` is true for a destructive ``op``."""
    if not acked:
        sys.stderr.write(f"refusing to run destructive op {op!r} without {DESTRUCTIVE_FLAG}\n")
        raise SystemExit(2)
