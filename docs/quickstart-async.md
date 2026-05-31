---
description: Async quickstart — open an analyser, poll channels, and stream readings.
---

# Quickstart (async)

```python
import anyio
from servomexlib import open_device, ProtocolKind


async def main() -> None:
    async with await open_device("COM11", protocol=ProtocolKind.CONTINUOUS_ASCII) as anz:
        info = await anz.identify()
        print(info.model, [c.channel for c in info.channels])

        frame = await anz.poll()
        for reading in frame.readings:
            print(reading.channel, reading.value, reading.unit)

        async for sample in anz.stream():
            print(sample.channel, sample.value, sample.unit)


anyio.run(main)
```

See [Design](design.md) §7.
