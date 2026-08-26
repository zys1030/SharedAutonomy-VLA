# Support tools

Language: English | [简体中文](README.zh-CN.md)

This directory contains commissioning, diagnostics, benchmarking, and
experiment-support utilities. The stable collection, export, policy serving,
and rollout entry points remain in `scripts/`.

The tools are grouped by purpose:

- `hardware/`: device inspection, read-only benchmarks, and guarded manual
  verification procedures;
- `inference/`: ACT protocol and server-connection diagnostics;
- `data/`: offline dataset and episode-pool checks;
- `training/`: support utilities for the ACT critical-frame baseline.

Install the project and the relevant optional dependencies before running a
tool. For example:

```powershell
pip install -e ".[hardware]"
python tools/hardware/rm65/check_connection.py --help
```

Hardware tools are engineering references validated on the project's
RM65/SpaceMouse/RealSense stack. They are not general-purpose hardware drivers.
Read [hardware/README.md](hardware/README.md) before using any tool that can
move an actuator.
