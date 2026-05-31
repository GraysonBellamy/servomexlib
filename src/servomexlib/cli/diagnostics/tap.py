"""``servomex-diag tap`` — passively print raw continuous frames (read-only).

Subscribes to the unsolicited broadcast and prints each frame's decoded channels,
useful for eyeballing a live link. Driveable from a ``--fixture`` capture.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from servomexlib.cli._common import open_analyzer

if TYPE_CHECKING:
    import argparse

__all__ = ["run"]


async def run(args: argparse.Namespace) -> int:
    """Print up to ``--count`` frames' samples as they arrive."""
    written = 0
    async with open_analyzer(args) as anz, anz.stream() as samples:
        async for sample in samples:
            if sample.reading is not None and sample.channel is not None:
                value = "None" if sample.reading.value is None else f"{sample.reading.value:g}"
                sys.stdout.write(
                    f"{sample.channel.value:<3} {value:>10} {sample.reading.unit.value}\n"
                )
            written += 1
            if written >= args.count:
                break
    return 0
