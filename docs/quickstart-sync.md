---
description: Sync quickstart — the blocking facade for scripts and REPL use.
---

# Quickstart (sync)

```python
from servomexlib.sync import Servomex

with Servomex.open("COM11", protocol="modbus_rtu", address=30) as anz:
    print(anz.poll())
    anz.start_calibration(1, confirm=True)
```

The sync facade wraps the async core through an `anyio` `BlockingPortal`.
See [Design](design.md) §7.
