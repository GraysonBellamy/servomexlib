---
description: Hardware-free testing — FakeTransport, recorded fixtures, and the MockSlave-backed Modbus fake.
---

# Testing

`servomexlib` needs no hardware to develop or test. `FakeTransport` implements
the transport in-process from a scripted write→reply map and drives **all
three** modes. The Modbus path is exercised byte-accurately by preloading
`anymodbus.testing.MockSlave` with the 4100's register/coil banks.

See [Design](design.md) §10.
