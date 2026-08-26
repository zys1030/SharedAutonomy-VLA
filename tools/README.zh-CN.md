# 支持工具

语言：[English](README.md) | 简体中文

本目录包含联调、诊断、benchmark 和实验支持工具。稳定的采集、导出、策略服务与 rollout 入口保留在 `scripts/` 中。

工具按用途划分：

- `hardware/`：设备检查、只读 benchmark 和带门控的人工验证流程；
- `inference/`：ACT 协议与服务连接诊断；
- `data/`：离线数据集与 episode pool 检查；
- `training/`：ACT critical-frame 基线的支持工具。

运行工具前先安装项目和对应的可选依赖。例如：

```powershell
pip install -e ".[hardware]"
python tools/hardware/rm65/check_connection.py --help
```

硬件工具是在本项目 RM65、SpaceMouse 和 RealSense 平台上验证的工程参考，不是通用硬件驱动。使用任何可能移动执行器的工具前，必须先阅读 [`hardware/README.zh-CN.md`](hardware/README.zh-CN.md)。
