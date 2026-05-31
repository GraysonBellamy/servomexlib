---
description: Troubleshooting connections, protocol detection, and common errors.
---

# Troubleshooting

- **No frames in continuous mode** — confirm the analyser's frame-frequency is
  non-zero and the baud/framing match (default 19200 8-N-1).
- **`AUTO` raises `ServomexConnectionError`** — the analyser may be silent to
  the probed mode; the modes are mutually exclusive and front-panel selected.
- **Modbus timeouts on the first transaction** — RS485 cold-start turnaround;
  the `AUTO` ladder retries, and a startup settle is applied.

Expanded as features land. See [Design](design.md) §4.4 and §9.
