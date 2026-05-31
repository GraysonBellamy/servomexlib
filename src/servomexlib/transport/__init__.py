"""Transport layer — raw async serial bytes.

Defines :class:`~servomexlib.transport.base.SerialSettings` and the
:class:`Transport` Protocol (which must also satisfy ``anyio.abc.ByteStream``
so ``anymodbus`` can bind to it), the real ``SerialTransport``, and the
in-process ``FakeTransport`` seam.
"""

from __future__ import annotations
