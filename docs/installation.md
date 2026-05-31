---
description: Installing servomexlib and its optional Modbus / sink extras.
---

# Installation

```bash
pip install servomexlib                 # continuous-ASCII core
pip install "servomexlib[modbus]"       # + Modbus RTU
pip install "servomexlib[modbus-ascii]" # + Modbus ASCII, AUTO sniffing, FC08 diagnostics
pip install "servomexlib[parquet]"      # + Parquet sink
pip install "servomexlib[postgres]"     # + PostgreSQL sink
```

Requires Python 3.13+. The core depends only on `anyio` and `anyserial`; the
Modbus path adds `anymodbus`.
