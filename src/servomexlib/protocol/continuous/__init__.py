"""Continuous-ASCII mode — unsolicited broadcast.

``checksum`` and ``parser`` are pure; the background receive loop +
latest-frame cache + fan-out live in ``client``.
"""

from __future__ import annotations
