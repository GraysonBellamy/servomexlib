"""The :class:`Sample` long-format streaming row.

Defined on the device models (so :meth:`Frame.as_samples` has no import cycle)
and re-exported here for the family-conventional ``servomexlib.streaming.Sample``
import site.
"""

from __future__ import annotations

from servomexlib.devices.models import Sample

__all__ = ["Sample"]
