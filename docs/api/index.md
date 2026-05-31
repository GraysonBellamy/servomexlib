---
description: API reference for servomexlib, auto-generated from source docstrings via mkdocstrings-python.
---

# API reference

Auto-generated from source docstrings via
[mkdocstrings-python](https://mkdocstrings.github.io/python/).

## Top-level

- [`servomexlib`](servomexlib.md) — top-level re-exports.

## Subpackages

- [`servomexlib.transport`](transport.md) — `Transport`, `SerialTransport`, `FakeTransport`, `SerialSettings`.
- [`servomexlib.protocol`](protocol.md) — `ProtocolKind`, `ProtocolClient`, AUTO detection, continuous + Modbus clients.
- [`servomexlib.registry`](registry.md) — `ChannelSpec`, units, status decode, charset.
- [`servomexlib.devices`](devices.md) — `Analyzer`, `Session`, models, `open_device`, discovery, capabilities.
- [`servomexlib.manager`](manager.md) — `ServomexManager`.
- [`servomexlib.streaming`](streaming.md) — `Sample`, `StreamingSession`, `StreamMode`, `record()`.
- [`servomexlib.sinks`](sinks.md) — sink protocol + first-party sinks.
- [`servomexlib.sync`](sync.md) — sync facade over the async core.
- [`servomexlib.testing`](testing.md) — `FakeTransport`, fixtures, Modbus fake, canned frames.
- [`servomexlib.errors`](errors.md) — typed exception hierarchy and `ErrorContext`.
