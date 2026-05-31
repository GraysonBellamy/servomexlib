---
description: Streaming and recording — passive continuous subscribe and timed Modbus polling into sinks.
---

# Streaming

`stream()` yields `Sample` rows; the mode defaults per protocol — `AUTOPRINT`
(passive broadcast subscribe) in continuous mode, `POLL` (timed acquisition)
in Modbus mode. `record()` drives drift-free batched acquisition into the
[sinks](api/sinks.md).

See [Design](design.md) §4.2 and §7.
