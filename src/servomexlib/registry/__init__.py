"""The registry — one spine for both protocols.

``channels`` maps every channel to its addressing in both worlds; ``units``,
``status``, and ``charset`` complete the layer. The Modbus client reads
addresses here, the continuous parser reads ``kind`` here, and ``identify()``
walks it to report populated slots. One source of truth.
"""

from __future__ import annotations
