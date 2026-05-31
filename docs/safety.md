---
description: Safety model — confirm-gated stateful operations and capability gating.
---

# Safety

Persistent or disruptive operations (autocalibration) are
`SafetyTier.STATEFUL` and require `confirm=True`; the session raises
`ServomexConfirmationRequiredError` *before any byte is sent*. Operations a
mode cannot perform (e.g. `start_calibration` in continuous mode) raise
`ServomexProtocolUnsupportedError` at the capability gate.

See [Design](design.md) §4.3.
