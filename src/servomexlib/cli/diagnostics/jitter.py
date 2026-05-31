"""``servomex-diag jitter`` — measure inter-sample cadence (read-only).

Collects ``--count`` samples and reports the min / mean / max gap between their
monotonic timestamps — a quick read on broadcast cadence or poll jitter. Driveable
from a ``--fixture`` capture.
"""

from __future__ import annotations

import itertools
import sys
from typing import TYPE_CHECKING

from servomexlib.cli._common import open_analyzer

if TYPE_CHECKING:
    import argparse

__all__ = ["run"]

#: Minimum samples needed to measure at least one inter-sample gap.
_MIN_SAMPLES = 2


async def run(args: argparse.Namespace) -> int:
    """Print gap statistics over ``--count`` samples."""
    stamps: list[int] = []
    async with open_analyzer(args) as anz, anz.stream(rate_hz=args.rate) as samples:
        async for sample in samples:
            stamps.append(sample.monotonic_ns)
            if len(stamps) >= args.count:
                break

    if len(stamps) < _MIN_SAMPLES:
        sys.stdout.write("not enough samples to measure jitter\n")
        return 0

    gaps_ms = [(b - a) / 1_000_000.0 for a, b in itertools.pairwise(stamps)]
    sys.stdout.write(
        f"samples={len(stamps)} gap_ms min={min(gaps_ms):.3f} "
        f"mean={sum(gaps_ms) / len(gaps_ms):.3f} max={max(gaps_ms):.3f}\n"
    )
    return 0
