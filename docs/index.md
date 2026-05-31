---
description: Async Python driver for Servomex SERVOPRO 4000-series gas analysers — continuous-ASCII and Modbus RTU/ASCII behind one channel-oriented API.
---

# servomexlib

Async-first Python driver for [Servomex](https://www.servomex.com/) SERVOPRO
4000-series gas analysers (developed against a bench **4100D**). One
protocol-neutral, channel-oriented API decodes the analyser's three
communication modes — **Continuous ASCII**, **Modbus RTU**, and **Modbus
ASCII** — into a single set of frozen, typed models.

The authoritative architectural document is [Design](design.md). Every design
decision should be traceable to a section of the design doc.

- **Async core** on [`anyio`](https://anyio.readthedocs.io); a thin
  [sync facade](quickstart-sync.md) for scripts and REPL use.
- **No hardware needed** to develop or test — a `FakeTransport` + recorded
  fixtures drive every protocol and the CLI in CI.

See [Installation](installation.md) and [Quickstart (async)](quickstart-async.md).
