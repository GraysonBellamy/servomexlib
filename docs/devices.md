---
description: The Analyzer facade — opening analysers, reading channels, status, and identity.
---

# Analysers

The `Analyzer` is the public async context manager returned by `open_device`.
It exposes a uniform, protocol-neutral method set — `poll()`, `read_channel()`,
`read_all()`, `status()`, `analyser_status()`, `identify()`, `snapshot()`,
`stream()`, and (Modbus only) `start_calibration()` / `stop_calibration()` /
`calibration_status()`.

See [Design](design.md) §7.
