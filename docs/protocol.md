---
description: The three communication modes — Continuous ASCII, Modbus RTU, and Modbus ASCII.
---

# Communication modes

The 4100 speaks three mutually-exclusive, front-panel-selected modes, all
decoded into the same models:

- **Continuous ASCII** — an unsolicited `;`-delimited broadcast every
  *frame-frequency* seconds, terminated by a 16-bit additive checksum.
- **Modbus RTU** — binary Modbus, CRC-16 framed; FC04 input registers
  (IEEE-754 float32, high word first), FC02 discrete status, FC05/FC15 coils.
- **Modbus ASCII** — the same register map, `:`/hex/LRC/CRLF framed.

`ProtocolKind.AUTO` sniffs: probe Modbus, else passive-listen for a continuous
frame. See [Design](design.md) §3 and §4.
